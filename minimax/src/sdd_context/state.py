import asyncio
import json
from typing import ClassVar

from powercontext import Artifact, ArtifactDraft, ArtifactLineage, Artifacts

from .util import append_jsonl, utc_now


class TaskStateDraft(ArtifactDraft[dict]):
    family: ClassVar[str] = "sdd_task_state"


class TaskStateArtifact(Artifact[dict]):
    family: ClassVar[str] = "sdd_task_state"


class SeekdbArtifactBackend:
    def __init__(self, database):
        self.database = database

    async def add(self, draft):
        task_id = draft.content["task_id"]
        lineage = ArtifactLineage(sources=draft.sources, artifacts=draft.artifacts,
                                  memory_citations=draft.memory_citations)
        existing = self.database.execute(
            "SELECT revision FROM pc_artifact_revisions WHERE family=%s AND artifact_id=%s",
            (draft.family, task_id),
        )
        if existing:
            raise RuntimeError(f"PowerContext artifact already exists: {task_id}")
        artifact = TaskStateArtifact(artifact_id=task_id, revision=1, content=draft.content, lineage=lineage)
        self._insert(artifact)
        self._record("add", artifact)
        return artifact

    async def revise(self, artifact, draft):
        rows = self.database.execute(
            """SELECT revision FROM pc_artifact_revisions
            WHERE family=%s AND artifact_id=%s ORDER BY revision DESC LIMIT 1""",
            (artifact.family, artifact.artifact_id),
        )
        if not rows or rows[0][0] != artifact.revision:
            raise RuntimeError("PowerContext artifact revision conflict")
        lineage = ArtifactLineage(sources=draft.sources, artifacts=draft.artifacts,
                                  memory_citations=draft.memory_citations)
        revised = TaskStateArtifact(artifact_id=artifact.artifact_id, revision=artifact.revision + 1,
                                    content=draft.content, lineage=lineage)
        self._insert(revised)
        self._record("revise", revised)
        return revised

    async def get(self, artifact):
        rows = self.database.execute(
            """SELECT artifact_id, revision, content_json, lineage_json FROM pc_artifact_revisions
            WHERE family=%s AND artifact_id=%s AND revision=%s""",
            (artifact.family, artifact.artifact_id, artifact.revision),
        )
        if not rows:
            raise LookupError(artifact.artifact_id)
        return self._from_row(rows[0])

    async def latest(self, artifact):
        rows = self.database.execute(
            """SELECT artifact_id, revision, content_json, lineage_json FROM pc_artifact_revisions
            WHERE family=%s AND artifact_id=%s ORDER BY revision DESC LIMIT 1""",
            (artifact.family, artifact.artifact_id),
        )
        if not rows:
            raise LookupError(artifact.artifact_id)
        latest = self._from_row(rows[0])
        self._record("latest", latest)
        return latest

    async def revisions(self, artifact):
        rows = self.database.execute(
            """SELECT artifact_id, revision, content_json, lineage_json FROM pc_artifact_revisions
            WHERE family=%s AND artifact_id=%s ORDER BY revision""",
            (artifact.family, artifact.artifact_id),
        )
        return tuple(self._from_row(row) for row in rows)

    def _insert(self, artifact):
        self.database.execute(
            """INSERT INTO pc_artifact_revisions
                (family, artifact_id, revision, content_json, lineage_json, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)""",
            (artifact.family, artifact.artifact_id, artifact.revision,
             json.dumps(artifact.content, ensure_ascii=False), artifact.lineage.model_dump_json(), utc_now()),
        )

    def _from_row(self, row):
        return TaskStateArtifact(artifact_id=row[0], revision=row[1], content=json.loads(row[2]),
                                 lineage=ArtifactLineage.model_validate_json(row[3]))

    def _record(self, operation, artifact):
        append_jsonl(
            self.database.records_dir / "powercontext/operations.jsonl",
            {"timestamp": utc_now(), "library": "powercontext", "operation": operation,
             "family": artifact.family, "artifact_id": artifact.artifact_id, "revision": artifact.revision},
        )


class TaskStateRepository:
    def __init__(self, database):
        backend = SeekdbArtifactBackend(database)
        self.artifacts = Artifacts(catalog=backend, store=backend)

    def save(self, task_id, state):
        return asyncio.run(self._save(task_id, state))

    def load(self, task_id):
        return asyncio.run(self._load(task_id))

    async def _save(self, task_id, state):
        selector = TaskStateArtifact(artifact_id=task_id, revision=1, content={"task_id": task_id})
        try:
            current = await self.artifacts.latest(selector)
        except LookupError:
            artifact = await self.artifacts.add(TaskStateDraft(content=state))
        else:
            artifact = await self.artifacts.revise(current, TaskStateDraft(content=state))
        return {"artifact_id": artifact.artifact_id, "revision": artifact.revision, "content": artifact.content}

    async def _load(self, task_id):
        selector = TaskStateArtifact(artifact_id=task_id, revision=1, content={"task_id": task_id})
        try:
            artifact = await self.artifacts.latest(selector)
        except LookupError:
            return None
        return {"artifact_id": artifact.artifact_id, "revision": artifact.revision, "content": artifact.content}
