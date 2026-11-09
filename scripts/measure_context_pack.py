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

# ruff: noqa: TRY003 - diagnostic failures include an actionable wire check

"""Measure a native Runtime sequence using an explicit, external workload.

Run with Python 3.12 and the competition dependencies. The JSON input contains
``turns`` (text strings), ``initial_turns`` (a count), ``max_bytes``, and a list
of ``rounds`` with ``query`` and optional ``append`` (turn indices).
This is a local diagnostic, not the organizer's unpublished cost sequence.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
from pathlib import Path

from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, PrepareContextRequest, open_builtin_runtime
from powercontext.builtin.runtime.context_pack import ContextPack, ContextPackDelta, apply_context_pack_delta
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import ContentSource


class _TurnPipeline:
    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(kind="conversation-turn", text=source.content, sources=(source,))
            for source in request.sources
            if isinstance(source, ContentSource)
        )


async def measure(workload: dict) -> tuple[dict, list[str]]:  # noqa: C901 - sequence and wire validation
    rows = []
    bodies = []
    base = None
    async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=_TurnPipeline()) as runtime:
        scope = await runtime.scopes.create(
            ScopeDraft(title="Wire measurement", summary="Local diagnostic", idempotency_key="wire-measurement")
        )
        sources = runtime.sources.for_scope(scope.scope_id)
        memory = runtime.memory.for_scope(scope.scope_id)

        async def capture(indices: list[int]) -> None:
            for index in indices:
                await sources.capture(
                    CaptureSource(source_id=f"turn-{index}", content=workload["turns"][index], metadata={})
                )
            while (await memory.flush()).processed:
                pass

        await capture(list(range(workload["initial_turns"])))
        for index, step in enumerate(workload["rounds"]):
            if step.get("append"):
                await capture(step["append"])
            # Use a fresh application on every round, as a real client can.
            context = runtime.context.for_scope(scope.scope_id)
            request = PrepareContextRequest(query=step["query"], max_bytes=workload["max_bytes"])
            prepared = await context.prepare(request)
            body = prepared.content or ""
            raw = body.encode("utf-8")
            sent = await context.prepare_incremental(request, base_pack=base)
            wire = sent.to_bytes()
            if base is None:
                if not isinstance(sent, ContextPack):
                    raise TypeError("first transmission must be a full pack")
                rebuilt = ContextPack.from_bytes(wire)
                prefix = 0
            else:
                if not isinstance(sent, ContextPackDelta):
                    raise TypeError("subsequent transmissions must be deltas")
                decoded = ContextPackDelta.from_bytes(wire)
                rebuilt = apply_context_pack_delta(ContextPack.from_bytes(base.to_bytes()), decoded)
                prefix = decoded.prefix_items
            if b"".join(rebuilt.items) != raw:
                raise ValueError("reconstructed content differs from ordinary prepare")
            if rebuilt != ContextPack.from_content(body):
                raise ValueError("reconstructed chunks or digest differ from the target pack")
            if len(wire) != sent.byte_count:
                raise ValueError("reported transmission size differs from actual bytes")
            rows.append({
                "round": index + 1,
                "query": step["query"],
                "raw_full_bytes": len(raw),
                "full_v1_pack_bytes": len(rebuilt.to_bytes()),
                "gzip_bytes": len(gzip.compress(raw, compresslevel=9, mtime=0)),
                "incremental_bytes": len(wire),
                "prefix_items": prefix,
                "content_sha256": hashlib.sha256(raw).hexdigest(),
            })
            bodies.append(body)
            base = rebuilt

    totals = {
        key: sum(row[key] for row in rows)
        for key in ("raw_full_bytes", "full_v1_pack_bytes", "gzip_bytes", "incremental_bytes")
    }
    for baseline in ("raw_full_bytes", "full_v1_pack_bytes", "gzip_bytes"):
        totals[f"saving_vs_{baseline}"] = 1 - totals["incremental_bytes"] / totals[baseline]
    return {
        "measurement": "local diagnostic; not the official hidden sequence",
        "round_count": len(rows),
        "nonempty_rounds": sum(row["raw_full_bytes"] > 0 for row in rows),
        "distinct_contexts": len({row["content_sha256"] for row in rows}),
        "totals": totals,
        "rounds": rows,
    }, bodies


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workload", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bodies", type=Path)
    args = parser.parse_args()
    result, bodies = asyncio.run(measure(json.loads(args.workload.read_text(encoding="utf-8"))))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.bodies:
        args.bodies.write_text(json.dumps(bodies, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "rounds"}, indent=2))
