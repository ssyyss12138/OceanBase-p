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

import json

import pytest

from powercontext.builtin.runtime.context_pack import (
    ContextPack,
    ContextPackDelta,
    ContextPackError,
    apply_context_pack_delta,
)


@pytest.mark.parametrize("text", ["\ud800", "valid\n\udfff", "x" * 600 + "\ud800"])
def test_invalid_unicode_text_raises_protocol_error(text: str) -> None:
    with pytest.raises(ContextPackError):
        ContextPack.from_content(text)


@pytest.mark.parametrize("limit", [1, 2, 3, 4, 7, 512])
@pytest.mark.parametrize("text", ["", "a\r\nb\n", "海洋🌊\n" * 300, "abc\u2028def\tghi"])
def test_chunk_limit_utf8_and_exact_content(limit: int, text: str) -> None:
    if any(len(character.encode("utf-8")) > limit for character in text):
        with pytest.raises(ContextPackError):
            ContextPack.from_content(text, chunk_bytes=limit)
        return
    pack = ContextPack.from_content(text, chunk_bytes=limit)
    assert b"".join(pack.items) == text.encode("utf-8")
    assert all(0 < len(item) <= limit for item in pack.items)
    assert all(item.decode("utf-8").encode("utf-8") == item for item in pack.items)
    assert ContextPack.from_content(text.encode("utf-8"), chunk_bytes=limit) == pack
    assert ContextPack.from_bytes(pack.to_bytes()) == pack


@pytest.mark.parametrize("appended", [["!!"], ["/w=="], ["YR=="], [123], "YWJj"])
def test_invalid_base64_and_utf8_wire_items_raise_protocol_error(appended: object) -> None:
    base = ContextPack.build(())
    payload = json.loads(ContextPackDelta.between(base, base).to_bytes())
    payload["appended"] = appended
    with pytest.raises(ContextPackError):
        ContextPackDelta.from_bytes(json.dumps(payload).encode())


def test_every_valid_prefix_reconstructs_the_exact_target_pack() -> None:
    base = ContextPack.build((b"alpha", "海洋".encode(), b"old"))
    target = ContextPack.build((b"alpha", "海洋".encode(), b"new"))
    for prefix in range(3):
        delta = ContextPackDelta(base.digest, target.digest, prefix, target.items[prefix:])
        rebuilt = apply_context_pack_delta(
            ContextPack.from_bytes(base.to_bytes()), ContextPackDelta.from_bytes(delta.to_bytes())
        )
        assert rebuilt.to_bytes() == target.to_bytes()
