# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Dependency-aware Source lifecycle for Memory and Topic Memory evidence."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.persistence.tables import (
    SOURCE_LIFECYCLE_EVENTS_TABLE,
    SOURCE_LIFECYCLE_NODES_TABLE,
)
from powercontext.builtin.runtime.models import GetMemoryEntryRequest
from powercontext.builtin.sources import validate_scope_id
from powercontext.errors import ArtifactNotFoundError
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime

_MEMORY_REASON = "source lifecycle"
_POLICIES = frozenset({"all", "any"})


class SourceLifecycleError(ValueError):
    """A lifecycle registration, event, or lookup violates the public contract."""


class SourceLifecycleEvent(BaseModel):
    """One immutable Source availability transition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str = Field(strict=True)
    revision: int = Field(gt=0, strict=True)
    sequence: int = Field(gt=0, strict=True)
    available: bool = Field(strict=True)
    reason: str = Field(strict=True)

    @field_validator("node_id", "reason")
    @classmethod
    def require_visible_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("lifecycle event text must not be blank")  # noqa: TRY003
        return value


@dataclass(frozen=True, slots=True)
class _Node:
    scope_id: str
    node_id: str
    kind: Literal["source", "memory", "topic"]
    policy: str | None
    parents: tuple[str, ...]
    reference: SourceRef | MemoryCitation | ArtifactRef
    source_revision: int | None
    active: bool
    ordinal: int


class SourceLifecycleApplication:
    """Select the lifecycle service for one scope."""

    def __init__(self, runtime: BuiltinRuntime) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycle:
        return ScopedSourceLifecycle(self._runtime, scope_id)


class ScopedSourceLifecycle:
    """Register an evidence DAG, apply Source events, and resolve exact refs."""

    def __init__(self, runtime: BuiltinRuntime, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def register_source(self, node_id: str, source_ref: SourceRef, *, revision: int) -> None:
        self._require_node_id(node_id)
        if not isinstance(source_ref, SourceRef):
            raise SourceLifecycleError("source_ref must be a SourceRef")  # noqa: TRY003
        if type(revision) is not int or revision < 1:
            raise SourceLifecycleError("source revision must be a positive integer")  # noqa: TRY003
        try:
            await self._runtime.records.for_scope(self.scope_id).get_source(
                source_ref.source_type,
                source_ref.source_id,
            )
        except Exception as error:
            raise SourceLifecycleError("source reference is not readable") from error  # noqa: TRY003
        await self._register_node(
            node_id,
            kind="source",
            reference=source_ref,
            parents=(),
            policy=None,
            source_revision=revision,
        )

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        *,
        parents: Sequence[str],
        policy: str,
    ) -> None:
        self._require_node_id(node_id)
        if not isinstance(citation, MemoryCitation):
            raise SourceLifecycleError("citation must be a MemoryCitation")  # noqa: TRY003
        normalized_parents = self._require_parents(node_id, parents)
        normalized_policy = self._require_policy(policy)
        try:
            await self._runtime.memory.for_scope(self.scope_id).get(
                GetMemoryEntryRequest(citation=citation),
            )
        except Exception as error:
            raise SourceLifecycleError("memory citation is not readable") from error  # noqa: TRY003
        await self._register_node(
            node_id,
            kind="memory",
            reference=citation,
            parents=normalized_parents,
            policy=normalized_policy,
            source_revision=None,
        )

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        *,
        parents: Sequence[str],
        policy: str,
    ) -> None:
        self._require_node_id(node_id)
        if not isinstance(artifact_ref, ArtifactRef) or artifact_ref.family != "topic-memory":
            raise SourceLifecycleError("artifact_ref must address Topic Memory")  # noqa: TRY003
        normalized_parents = self._require_parents(node_id, parents)
        normalized_policy = self._require_policy(policy)
        try:
            await self._runtime.records.for_scope(self.scope_id).get_artifact_revision(
                "topic-memory",
                artifact_ref.artifact_id,
                artifact_ref.revision,
            )
        except Exception as error:
            raise SourceLifecycleError("topic artifact revision is not readable") from error  # noqa: TRY003
        await self._register_node(
            node_id,
            kind="topic",
            reference=artifact_ref,
            parents=normalized_parents,
            policy=normalized_policy,
            source_revision=None,
        )

    async def apply(self, event: SourceLifecycleEvent) -> list[str]:
        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("event must be a SourceLifecycleEvent")  # noqa: TRY003
        async with self._runtime._scope_operation(self.scope_id):  # noqa: SIM117 - one scope lease, one transaction
            async with self._require_database().transaction() as connection:
                nodes = await self._load_nodes(connection)
                by_id = {node.node_id: node for node in nodes}
                source = by_id.get(event.node_id)
                if source is None or source.kind != "source":
                    raise SourceLifecycleError("event node is not a registered Source")  # noqa: TRY003
                if source.source_revision != event.revision:
                    raise SourceLifecycleError("event revision does not match the Source")  # noqa: TRY003
                latest = await self._latest_event(connection, event.node_id)
                exact = await self._load_event(connection, event.node_id, event.sequence)
                if exact is not None:
                    if not _same_event(exact, event):
                        raise SourceLifecycleError("event sequence already has different content")  # noqa: TRY003
                    if latest is not None and int(latest["sequence"]) > event.sequence:
                        raise SourceLifecycleError("event sequence is older than the current Source state")  # noqa: TRY003
                else:
                    if latest is not None and event.sequence <= int(latest["sequence"]):
                        raise SourceLifecycleError("event sequence must increase")  # noqa: TRY003
                    await connection.execute(
                        insert(SOURCE_LIFECYCLE_EVENTS_TABLE).values(
                            scope_id=self.scope_id,
                            node_id=event.node_id,
                            sequence=event.sequence,
                            revision=event.revision,
                            available=event.available,
                            reason=event.reason,
                        )
                    )
                values = _active_values(nodes, {source.node_id: event.available})
                nodes = await self._store_active_values(connection, nodes, values)
        await self._reconcile_memory(nodes)
        return sorted(node_id for node_id, active in values.items() if active)

    async def resolve(
        self,
        node_id: str,
        *,
        purpose: Literal["generation", "audit"],
    ) -> SourceRef | MemoryCitation | ArtifactRef:
        self._require_node_id(node_id)
        if not isinstance(purpose, str) or purpose not in {"generation", "audit"}:
            raise SourceLifecycleError("unknown lifecycle resolution purpose")  # noqa: TRY003
        async with self._runtime._scope_operation(self.scope_id):  # noqa: SIM117 - one scope lease, one transaction
            async with self._require_database().transaction() as connection:
                node = next(
                    (item for item in await self._load_nodes(connection) if item.node_id == node_id),
                    None,
                )
        if node is None:
            raise SourceLifecycleError("lifecycle node is not registered")  # noqa: TRY003
        if purpose == "generation" and not node.active:
            raise SourceLifecycleError("lifecycle node is not available for generation")  # noqa: TRY003
        return node.reference

    async def inactive_topic_refs(self) -> frozenset[tuple[str, str, int]]:
        """Return exact Topic revisions hidden from search and context."""

        if getattr(self._runtime, "_lifecycle_database", None) is None:
            return frozenset()
        async with self._runtime._scope_operation(self.scope_id):  # noqa: SIM117 - one scope lease, one transaction
            async with self._require_database().transaction() as connection:
                rows = (
                    await connection.execute(
                        select(SOURCE_LIFECYCLE_NODES_TABLE).where(
                            SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id,
                            SOURCE_LIFECYCLE_NODES_TABLE.c.kind == "topic",
                            SOURCE_LIFECYCLE_NODES_TABLE.c.active.is_(False),
                        )
                    )
                ).mappings()
                refs = []
                for row in rows:
                    reference = _decode_reference("topic", str(row["reference"]))
                    if not isinstance(reference, ArtifactRef):
                        raise SourceLifecycleError("stored lifecycle topic reference is invalid")  # noqa: TRY003
                    refs.append((reference.family, reference.artifact_id, reference.revision))
        return frozenset(refs)

    async def _register_node(
        self,
        node_id: str,
        *,
        kind: Literal["source", "memory", "topic"],
        reference: SourceRef | MemoryCitation | ArtifactRef,
        parents: tuple[str, ...],
        policy: str | None,
        source_revision: int | None,
    ) -> None:
        async with self._runtime._scope_operation(self.scope_id):  # noqa: SIM117 - one scope lease, one transaction
            async with self._require_database().transaction() as connection:
                nodes = await self._load_nodes(connection)
                existing = next((node for node in nodes if node.node_id == node_id), None)
                if existing is not None:
                    if not _same_registration(
                        existing,
                        kind=kind,
                        reference=reference,
                        parents=parents,
                        policy=policy,
                        source_revision=source_revision,
                    ):
                        raise SourceLifecycleError("node id is already bound to different evidence")  # noqa: TRY003
                    registered = nodes
                else:
                    if kind == "source":
                        active = True
                    else:
                        if not parents:
                            raise SourceLifecycleError("derived nodes require parents")  # noqa: TRY003
                        by_id = {node.node_id: node for node in nodes}
                        if any(parent not in by_id for parent in parents):
                            raise SourceLifecycleError("parent node is not registered")  # noqa: TRY003
                        values = _active_values(nodes)
                        support = tuple(values[parent] for parent in parents)
                        active = all(support) if policy == "all" else any(support)
                    ordinal = max((node.ordinal for node in nodes), default=-1) + 1
                    registered_node = _Node(
                        scope_id=self.scope_id,
                        node_id=node_id,
                        kind=kind,
                        policy=policy,
                        parents=parents,
                        reference=reference,
                        source_revision=source_revision,
                        active=active,
                        ordinal=ordinal,
                    )
                    await connection.execute(
                        insert(SOURCE_LIFECYCLE_NODES_TABLE).values(
                            scope_id=self.scope_id,
                            node_id=node_id,
                            kind=kind,
                            policy=policy,
                            parents=_parents_json(parents),
                            reference=_reference_json(reference),
                            source_revision=source_revision,
                            active=active,
                            ordinal=ordinal,
                        )
                    )
                    registered = (*nodes, registered_node)
        if kind == "memory":
            await self._reconcile_memory(registered)

    async def _reconcile_memory(self, nodes: Sequence[_Node]) -> None:
        targets = {
            node.reference.entry_id: node.active
            for node in nodes
            if node.kind == "memory" and isinstance(node.reference, MemoryCitation)
        }
        if not targets:
            return
        # A default (non-assembly) Context prepare caches the first-seen Memory
        # hits on the Runtime.  Eligibility may have changed, so rebuild that
        # selection from the native active head on the next prepare.
        self._runtime._context_memory_pool.pop(self.scope_id, None)
        async with (
            self._runtime._context(self.scope_id) as context,
            self._runtime._locked(self.scope_id),
        ):
            service = context.artifacts.memory
            try:
                head = await service.head(context.artifacts.memory_artifact_id)
            except ArtifactNotFoundError:
                return
            deactivate = await self._state_changes(head, service.entries, targets, target_state="inactive")
            if deactivate:
                head = await service.forget(
                    head,
                    entries=deactivate,
                    reason=_MEMORY_REASON,
                )
            reactivate = await self._state_changes(head, service.entries, targets, target_state="active")
            if reactivate:
                await service.reactivate(
                    head,
                    entries=reactivate,
                    reason=_MEMORY_REASON,
                )

    async def _state_changes(self, head, entries_loader, targets, *, target_state: str):
        manifest = {item.entry_id: item for item in head.content.manifest.entries}
        versions = {entry.entry_id: entry for entry in await entries_loader(head)}
        current_state = "active" if target_state == "inactive" else "inactive"
        return tuple(
            versions[entry_id]
            for entry_id, active in targets.items()
            if entry_id in manifest
            and entry_id in versions
            and manifest[entry_id].state == current_state
            and (active is (target_state == "active"))
        )

    async def _load_nodes(self, connection: AsyncConnection) -> tuple[_Node, ...]:
        rows = (
            await connection.execute(
                select(SOURCE_LIFECYCLE_NODES_TABLE)
                .where(SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id)
                .order_by(SOURCE_LIFECYCLE_NODES_TABLE.c.ordinal, SOURCE_LIFECYCLE_NODES_TABLE.c.node_id)
            )
        ).mappings()
        return tuple(_decode_node(row) for row in rows)

    async def _store_active_values(
        self,
        connection: AsyncConnection,
        nodes: Sequence[_Node],
        values: dict[str, bool],
    ) -> tuple[_Node, ...]:
        updated: list[_Node] = []
        for node in nodes:
            active = values[node.node_id]
            if node.active != active:
                await connection.execute(
                    update(SOURCE_LIFECYCLE_NODES_TABLE)
                    .where(
                        SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id,
                        SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == node.node_id,
                    )
                    .values(active=active)
                )
                node = replace(node, active=active)
            updated.append(node)
        return tuple(updated)

    async def _latest_event(self, connection: AsyncConnection, node_id: str):
        return (
            (
                await connection.execute(
                    select(SOURCE_LIFECYCLE_EVENTS_TABLE)
                    .where(
                        SOURCE_LIFECYCLE_EVENTS_TABLE.c.scope_id == self.scope_id,
                        SOURCE_LIFECYCLE_EVENTS_TABLE.c.node_id == node_id,
                    )
                    .order_by(SOURCE_LIFECYCLE_EVENTS_TABLE.c.sequence.desc())
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )

    async def _load_event(self, connection: AsyncConnection, node_id: str, sequence: int):
        return (
            (
                await connection.execute(
                    select(SOURCE_LIFECYCLE_EVENTS_TABLE).where(
                        SOURCE_LIFECYCLE_EVENTS_TABLE.c.scope_id == self.scope_id,
                        SOURCE_LIFECYCLE_EVENTS_TABLE.c.node_id == node_id,
                        SOURCE_LIFECYCLE_EVENTS_TABLE.c.sequence == sequence,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )

    def _require_database(self):
        database = getattr(self._runtime, "_lifecycle_database", None)
        if database is None:
            raise SourceLifecycleError("lifecycle database is unavailable")  # noqa: TRY003
        return database

    @staticmethod
    def _require_policy(policy: object) -> str:
        if not isinstance(policy, str) or policy not in _POLICIES:
            raise SourceLifecycleError("policy must be 'all' or 'any'")  # noqa: TRY003
        return policy

    @staticmethod
    def _require_parents(node_id: str, parents: object) -> tuple[str, ...]:
        if isinstance(parents, str) or not isinstance(parents, Sequence):
            raise SourceLifecycleError("parents must be a sequence of node ids")  # noqa: TRY003
        values = tuple(parents)
        if not values:
            raise SourceLifecycleError("derived nodes require at least one parent")  # noqa: TRY003
        if len(set(values)) != len(values):
            raise SourceLifecycleError("parent nodes must be unique")  # noqa: TRY003
        for value in values:
            if not isinstance(value, str) or not value.strip():
                raise SourceLifecycleError("parent node ids must be nonblank text")  # noqa: TRY003
            if value == node_id:
                raise SourceLifecycleError("a node cannot depend on itself")  # noqa: TRY003
        return values

    @staticmethod
    def _require_node_id(node_id: object) -> None:
        if not isinstance(node_id, str) or not node_id.strip() or len(node_id) > 256:
            raise SourceLifecycleError("node id must be nonblank text within the identity limit")  # noqa: TRY003


def _active_values(nodes: Sequence[_Node], source_override: dict[str, bool] | None = None) -> dict[str, bool]:
    values = {node.node_id: node.active for node in nodes}
    if source_override:
        values.update(source_override)
    for _ in range(len(nodes) + 1):
        changed = False
        for node in nodes:
            if node.kind == "source":
                desired = values[node.node_id]
            else:
                support = tuple(values[parent] for parent in node.parents)
                desired = all(support) if node.policy == "all" else any(support)
            if values[node.node_id] != desired:
                values[node.node_id] = desired
                changed = True
        if not changed:
            break
    return values


def _decode_node(row) -> _Node:
    kind = str(row["kind"])
    policy = None if row["policy"] is None else str(row["policy"])
    return _Node(
        scope_id=str(row["scope_id"]),
        node_id=str(row["node_id"]),
        kind=kind,  # type: ignore[arg-type]
        policy=policy,
        parents=tuple(json.loads(str(row["parents"]))),
        reference=_decode_reference(kind, str(row["reference"])),
        source_revision=None if row["source_revision"] is None else int(row["source_revision"]),
        active=bool(row["active"]),
        ordinal=int(row["ordinal"]),
    )


def _decode_reference(kind: str, payload: str) -> SourceRef | MemoryCitation | ArtifactRef:
    try:
        value = json.loads(payload)
        if kind == "source":
            return SourceRef.model_validate(value)
        if kind == "memory":
            return MemoryCitation.model_validate(value)
        if kind == "topic":
            return ArtifactRef.model_validate(value)
    except Exception as error:
        raise SourceLifecycleError("stored lifecycle reference is invalid") from error  # noqa: TRY003
    raise SourceLifecycleError("stored lifecycle node kind is invalid")  # noqa: TRY003


def _reference_json(reference: SourceRef | MemoryCitation | ArtifactRef) -> str:
    return json.dumps(reference.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def _parents_json(parents: tuple[str, ...]) -> str:
    return json.dumps(list(parents), separators=(",", ":"))


def _same_event(row, event: SourceLifecycleEvent) -> bool:
    return (
        str(row["node_id"]) == event.node_id
        and int(row["revision"]) == event.revision
        and int(row["sequence"]) == event.sequence
        and bool(row["available"]) == event.available
        and str(row["reason"]) == event.reason
    )


def _same_registration(
    node: _Node,
    *,
    kind: str,
    reference: SourceRef | MemoryCitation | ArtifactRef,
    parents: tuple[str, ...],
    policy: str | None,
    source_revision: int | None,
) -> bool:
    return (
        node.kind == kind
        and node.parents == parents
        and node.policy == policy
        and node.source_revision == source_revision
        and _reference_json(node.reference) == _reference_json(reference)
    )


__all__ = ["ScopedSourceLifecycle", "SourceLifecycleApplication", "SourceLifecycleError", "SourceLifecycleEvent"]
