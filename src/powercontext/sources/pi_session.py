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

"""Captured evidence from one selected branch of a Pi v3 session."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, JsonValue, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.connectors import ConnectorRunCompletion, ConnectorRunSession, ConnectorRunStatus
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PI_EVENT_SOURCE_NAME = "pi-event"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
_CHECKPOINT_VERSION = 1


class PiSessionInput(BaseModel):
    """One complete Pi v3 JSONL archive and an optional selected leaf."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible event on the selected Pi branch."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_event(self) -> PiEventInput:
        _require_non_empty_string("session_id", self.session_id)
        _require_non_empty_string("event_id", self.event_id)
        _require_non_empty_string("event_type", self.event_type)
        _require_non_empty_string("role", self.role)
        if not self.content:
            raise ValueError("Pi event content must not be empty")  # noqa: TRY003
        if self.record.get("id") != self.event_id:
            raise ValueError("Pi event record id must match event_id")  # noqa: TRY003
        if self.record.get("parentId") != self.parent_id:
            raise ValueError("Pi event record parentId must match parent_id")  # noqa: TRY003
        if self.record.get("type") != self.event_type:
            raise ValueError("Pi event record type must match event_type")  # noqa: TRY003
        pi_event_source_id(self.session_id, self.event_id)
        return self


class PiEventSource(Source):
    """An immutable captured Pi event and its complete input record."""

    materialization: Literal[SourceMaterialization.CAPTURED] = SourceMaterialization.CAPTURED
    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_source(self) -> PiEventSource:
        event = PiEventInput(
            session_id=self.session_id,
            event_id=self.event_id,
            parent_id=self.parent_id,
            event_type=self.event_type,
            role=self.role,
            content=self.content,
            record=self.record,
        )
        expected = pi_event_source_id(event.session_id, event.event_id)
        if self.name != expected or self.source_id != expected:
            raise ValueError("Pi Source name and source_id must match its event identity")  # noqa: TRY003
        return self


@dataclass(frozen=True, slots=True)
class _ParsedPiSession:
    session_id: str
    records: dict[str, dict[str, JsonValue]]
    path: tuple[dict[str, JsonValue], ...]
    events: tuple[PiEventInput, ...]


@dataclass(frozen=True, slots=True)
class _CheckpointRecord:
    digest: str
    imported: bool


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Parse all Pi records, then return visible events from root to selected leaf."""

    return _parse_pi_session(value).events


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Return a stable delimiter-safe identity for one Pi session event."""

    _require_non_empty_string("session_id", session_id)
    _require_non_empty_string("event_id", event_id)
    source_id = f"pi:{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"Pi event source_id must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return source_id


class PiEventSourceAdapter:
    """Resolve and read captured Pi event Sources."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        source_id = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=source_id,
            source_id=source_id,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class PiEventTextEvidenceProjection:
    """Expose the complete Pi event text through the standard evidence schema."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.source_id,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


class PiSessionConnector:
    """Incrementally capture visible events from one Pi session file."""

    name = PI_SESSION_CONNECTOR_NAME
    version = "1"
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self.path = Path(path)
        self.leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        parsed = _parse_pi_session(PiSessionInput(content=self.path.read_bytes(), leaf_id=self.leaf_id))
        checkpoint = _parse_checkpoint(session.checkpoint)
        current_records = checkpoint.setdefault(parsed.session_id, {})
        _validate_checkpoint_history(parsed, current_records)

        imported = {event_id for event_id, state in current_records.items() if state.imported}
        for event in parsed.events:
            if event.event_id in imported:
                continue
            await session.submit(
                pi_event_source_id(event.session_id, event.event_id),
                PI_EVENT_SOURCE_NAME,
                event,
            )

        visible_event_ids = {event.event_id for event in parsed.events}
        for record in parsed.path:
            event_id = _record_id(record)
            current_records[event_id] = _CheckpointRecord(
                digest=_record_digest(record),
                imported=current_records.get(event_id, _CheckpointRecord("", False)).imported
                or event_id in visible_event_ids,
            )

        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint=_dump_checkpoint(checkpoint),
        )


def _parse_pi_session(value: PiSessionInput) -> _ParsedPiSession:  # noqa: C901
    try:
        text = value.content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Pi session content must be valid UTF-8") from error  # noqa: TRY003

    lines = text.splitlines()
    if not lines:
        raise ValueError("Pi session must contain a header")  # noqa: TRY003
    decoded = tuple(_load_json_object(line) for line in lines)
    header = decoded[0]
    version = header.get("version")
    if header.get("type") != "session" or type(version) is not int or version != 3:
        raise ValueError("Pi session header must declare type=session and version=3")  # noqa: TRY003
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("Pi session header id must be a non-empty string")  # noqa: TRY003

    records: dict[str, dict[str, JsonValue]] = {}
    order: list[str] = []
    for record in decoded[1:]:
        event_id = record.get("id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("Pi entries must have a non-empty string id")  # noqa: TRY003
        if event_id in records:
            raise ValueError("Pi entry ids must be unique")  # noqa: TRY003
        if "parentId" not in record:
            raise ValueError("Pi entries must contain parentId")  # noqa: TRY003
        parent_id = record["parentId"]
        if parent_id is not None and not isinstance(parent_id, str):
            raise ValueError("Pi entry parentId must be a string or null")  # noqa: TRY003
        if parent_id is not None and parent_id not in records:
            raise ValueError("Pi entry parent must refer to an earlier node")  # noqa: TRY003
        event_type = record.get("type")
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("Pi entries must have a non-empty string type")  # noqa: TRY003
        records[event_id] = record
        order.append(event_id)

    if value.leaf_id is None:
        leaf_id = order[-1] if order else None
    else:
        leaf_id = value.leaf_id
        if not isinstance(leaf_id, str) or not leaf_id:
            raise ValueError("Pi leaf_id must be a non-empty string")  # noqa: TRY003
        if leaf_id not in records:
            raise ValueError("Pi leaf_id does not exist")  # noqa: TRY003

    path = _selected_path(records, leaf_id)
    events = tuple(event for record in path if (event := _visible_event(session_id, record)) is not None)
    return _ParsedPiSession(session_id=session_id, records=records, path=path, events=events)


def _load_json_object(line: str) -> dict[str, JsonValue]:
    if not line.strip():
        raise ValueError("Pi JSONL must not contain empty records")  # noqa: TRY003
    try:
        value = json.loads(line, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as error:
        raise ValueError("Pi session contains invalid JSON") from error  # noqa: TRY003
    if not isinstance(value, dict):
        raise ValueError("Every Pi JSONL record must be an object")  # noqa: TRY003, TRY004
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")  # noqa: TRY003


def _selected_path(
    records: dict[str, dict[str, JsonValue]],
    leaf_id: str | None,
) -> tuple[dict[str, JsonValue], ...]:
    if leaf_id is None:
        return ()
    reversed_path: list[dict[str, JsonValue]] = []
    visited: set[str] = set()
    current: str | None = leaf_id
    while current is not None:
        if current in visited:
            raise ValueError("Pi session graph contains a cycle")  # noqa: TRY003
        visited.add(current)
        record = records[current]
        reversed_path.append(record)
        parent = record["parentId"]
        current = parent if isinstance(parent, str) else None
    return tuple(reversed(reversed_path))


def _visible_event(session_id: str, record: dict[str, JsonValue]) -> PiEventInput | None:
    event_type = record["type"]
    if event_type == "message":
        visible = _visible_message(record.get("message"))
    elif event_type in {"compaction", "branch_summary"}:
        summary = record.get("summary")
        visible = (event_type, summary) if isinstance(summary, str) and summary else None
    elif event_type == "custom_message":
        body = _visible_content(record.get("content"), allow_tool_calls=False)
        visible = ("custom", body) if body is not None else None
    else:
        visible = None
    if visible is None:
        return None
    role, body = visible
    return PiEventInput(
        session_id=session_id,
        event_id=_record_id(record),
        parent_id=_record_parent_id(record),
        event_type=event_type,
        role=role,
        content=f"{role}: {body}",
        record=record,
    )


def _visible_message(value: JsonValue | None) -> tuple[str, str] | None:
    if not isinstance(value, dict):
        return None
    role = value.get("role")
    if not isinstance(role, str) or not role:
        return None
    if role == "toolResult":
        text = _visible_content(value.get("content"), allow_tool_calls=False)
        if text is None:
            return None
        tool_name = _string_or_unknown(value.get("toolName"))
        tool_call_id = _string_or_unknown(value.get("toolCallId"))
        return role, f"[tool_result {tool_name} {tool_call_id}] {text}"
    if role == "bashExecution":
        command = value.get("command")
        output = value.get("output")
        if not isinstance(command, str):
            command = ""
        if not isinstance(output, str):
            output = ""
        if not command and not output:
            return None
        return role, f"$ {command}\n{output}"
    body = _visible_content(value.get("content"), allow_tool_calls=True)
    return (role, body) if body is not None else None


def _visible_content(value: JsonValue | None, *, allow_tool_calls: bool) -> str | None:
    if isinstance(value, str):
        return value if value else None
    if not isinstance(value, list):
        return None
    parts: list[str] = []
    visible = False
    for block in value:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text" and isinstance(block.get("text"), str):
            text = block["text"]
            parts.append(text)
            visible = visible or bool(text)
        elif allow_tool_calls and block_type == "toolCall":
            name = _string_or_unknown(block.get("name"))
            tool_call_id = _string_or_unknown(block.get("id"))
            arguments = block.get("arguments", {})
            encoded = json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            parts.append(f"[tool_call {name} {tool_call_id}] {encoded}")
            visible = True
    return "\n".join(parts) if visible else None


def _string_or_unknown(value: JsonValue | None) -> str:
    return value if isinstance(value, str) and value else "unknown"


def _record_id(record: dict[str, JsonValue]) -> str:
    value = record["id"]
    if not isinstance(value, str):
        raise ValueError("Pi record id must be a string")  # noqa: TRY003, TRY004
    return value


def _record_parent_id(record: dict[str, JsonValue]) -> str | None:
    value = record["parentId"]
    if value is not None and not isinstance(value, str):
        raise ValueError("Pi record parentId must be a string or null")  # noqa: TRY003
    return value


def _record_digest(record: dict[str, JsonValue]) -> str:
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _parse_checkpoint(value: JsonValue | None) -> dict[str, dict[str, _CheckpointRecord]]:
    if value is None:
        return {}
    if not isinstance(value, dict) or value.get("version") != _CHECKPOINT_VERSION:
        raise ValueError("Pi checkpoint has an unsupported shape or version")  # noqa: TRY003
    sessions = value.get("sessions")
    if not isinstance(sessions, dict):
        raise ValueError("Pi checkpoint sessions must be an object")  # noqa: TRY003, TRY004
    parsed: dict[str, dict[str, _CheckpointRecord]] = {}
    for session_id, session_value in sessions.items():
        if not isinstance(session_id, str) or not session_id or not isinstance(session_value, dict):
            raise ValueError("Pi checkpoint contains an invalid session")  # noqa: TRY003
        record_values = session_value.get("records")
        if not isinstance(record_values, dict):
            raise ValueError("Pi checkpoint records must be an object")  # noqa: TRY003, TRY004
        records: dict[str, _CheckpointRecord] = {}
        for event_id, record_value in record_values.items():
            if not isinstance(event_id, str) or not event_id or not isinstance(record_value, dict):
                raise ValueError("Pi checkpoint contains an invalid record")  # noqa: TRY003
            digest = record_value.get("digest")
            imported = record_value.get("imported")
            if not _valid_digest(digest) or not isinstance(imported, bool):
                raise ValueError("Pi checkpoint record state is invalid")  # noqa: TRY003
            records[event_id] = _CheckpointRecord(digest=digest, imported=imported)
        parsed[session_id] = records
    return parsed


def _valid_digest(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
        return False
    try:
        int(value.removeprefix("sha256:"), 16)
    except ValueError:
        return False
    return value == value.lower()


def _validate_checkpoint_history(
    parsed: _ParsedPiSession,
    checkpoint: dict[str, _CheckpointRecord],
) -> None:
    for event_id, state in checkpoint.items():
        record = parsed.records.get(event_id)
        if record is None:
            raise ValueError("A previously captured Pi record is missing")  # noqa: TRY003
        if _record_digest(record) != state.digest:
            raise ValueError("A previously captured Pi record was modified")  # noqa: TRY003


def _dump_checkpoint(checkpoint: dict[str, dict[str, _CheckpointRecord]]) -> dict[str, JsonValue]:
    return {
        "version": _CHECKPOINT_VERSION,
        "sessions": {
            session_id: {
                "records": {
                    event_id: {"digest": state.digest, "imported": state.imported}
                    for event_id, state in sorted(records.items())
                }
            }
            for session_id, records in sorted(checkpoint.items())
        },
    }


def _require_non_empty_string(field: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")  # noqa: TRY003


PI_EVENT_SOURCE_ADAPTER = PiEventSourceAdapter()
PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    PI_EVENT_SOURCE_ADAPTER,
    projections=(PiEventTextEvidenceProjection(),),
)


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PiEventInput",
    "PiEventSource",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
    "pi_event_source_id",
]
