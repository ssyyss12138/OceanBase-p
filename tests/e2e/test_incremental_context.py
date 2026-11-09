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

from __future__ import annotations

import asyncio
import json

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.runtime import (
    BuiltinConfig,
    PrepareContextRequest,
    RememberMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.context_pack import ContextPack, ContextPackDelta, apply_context_pack_delta
from powercontext.builtin.scope import ScopeDraft


def test_incremental_context_follows_current_query_and_can_become_empty() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig()) as runtime:
            scope = await runtime.scopes.create(
                ScopeDraft(title="Query changes", summary="Incremental compatibility", idempotency_key="query-changes")
            )
            memory = runtime.memory.for_scope(scope.scope_id)
            await memory.remember(
                RememberMemoryRequest(
                    entries=(
                        MemoryEntryInput(kind="fact", text="remote controls use infrared"),
                        MemoryEntryInput(kind="fact", text="banana trees grow outdoors"),
                    )
                )
            )
            base = None
            for query, expected in (
                ("remote", ["remote controls use infrared"]),
                ("banana", ["banana trees grow outdoors"]),
                ("zebraunmatched", []),
            ):
                context = runtime.context.for_scope(scope.scope_id)
                request = PrepareContextRequest(query=query)
                prepared = await context.prepare(request)
                if expected:
                    assert prepared.content is not None
                    items = json.loads(prepared.content.splitlines()[-2])["items"]
                    assert [item["content"] for item in items] == expected
                else:
                    assert prepared.content is None
                sent = await context.prepare_incremental(request, base_pack=base)
                if base is None:
                    assert isinstance(sent, ContextPack)
                    base = ContextPack.from_bytes(sent.to_bytes())
                else:
                    assert isinstance(sent, ContextPackDelta)
                    base = apply_context_pack_delta(
                        ContextPack.from_bytes(base.to_bytes()), ContextPackDelta.from_bytes(sent.to_bytes())
                    )
                assert base == ContextPack.from_content(prepared.content or "")
            assert base.items == ()

    asyncio.run(scenario())
