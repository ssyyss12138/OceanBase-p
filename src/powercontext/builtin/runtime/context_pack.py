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

# ruff: noqa: TRY003

"""Canonical full and incremental wire packs for prepared context."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

_PACK_SCHEMA = "powercontext.context-pack.v1"
_DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ContextPackError(ValueError):
    """A Context Pack or delta violates the public wire contract."""


@dataclass(frozen=True, slots=True)
class ContextPack:
    """A complete prepared-context body split into independently valid UTF-8 chunks."""

    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        """Build a validated pack and its canonical digest and wire byte count."""

        validated = _validate_items(items)
        encoded_items = _encode_items(validated)
        digest = _pack_digest(encoded_items)
        byte_count, _payload = _pack_payload(encoded_items, digest)
        return cls(items=validated, digest=digest, byte_count=byte_count)

    @classmethod
    def from_content(  # noqa: C901 - line and character boundary handling
        cls, content: str | bytes, *, chunk_bytes: int = 512
    ) -> ContextPack:
        """Deterministically split content without crossing UTF-8 character boundaries."""

        if type(chunk_bytes) is not int or chunk_bytes <= 0:
            raise ContextPackError("chunk_bytes must be a positive integer")
        if type(content) is str:
            text = content
        elif type(content) is bytes:
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ContextPackError("content must be valid UTF-8") from error
        else:
            raise ContextPackError("content must be text or bytes")

        chunks: list[bytes] = []
        current = bytearray()

        def append_text(value: str) -> None:
            nonlocal current
            for character in value:
                try:
                    encoded = character.encode("utf-8")
                except UnicodeEncodeError as error:
                    raise ContextPackError("content must be valid UTF-8") from error
                if len(encoded) > chunk_bytes:
                    raise ContextPackError("chunk limit cannot contain one UTF-8 character")
                if current and len(current) + len(encoded) > chunk_bytes:
                    chunks.append(bytes(current))
                    current.clear()
                current.extend(encoded)

        # Lines are stable anchors: appending a conversation turn changes only
        # the final line, so earlier chunks remain reusable even when a fixed
        # byte boundary would have shifted.
        for line in text.splitlines(keepends=True):
            encoded_line = line.encode("utf-8")
            if len(encoded_line) <= chunk_bytes:
                if current and len(current) + len(encoded_line) > chunk_bytes:
                    chunks.append(bytes(current))
                    current.clear()
                current.extend(encoded_line)
            else:
                if current:
                    chunks.append(bytes(current))
                    current.clear()
                append_text(line)
        if current:
            chunks.append(bytes(current))
        return cls.build(tuple(chunks))

    def to_bytes(self) -> bytes:
        """Serialize this pack in the canonical public JSON representation."""

        encoded_items = _encode_items(_validate_items(self.items))
        expected_digest = _pack_digest(encoded_items)
        if self.digest != expected_digest:
            raise ContextPackError("pack digest does not match its items")
        expected_count, payload = _pack_payload(encoded_items, expected_digest)
        if type(self.byte_count) is not int or self.byte_count != expected_count:
            raise ContextPackError("pack byte_count does not match its wire size")
        return payload

    @classmethod
    def from_bytes(cls, value: bytes) -> ContextPack:
        """Decode and fully validate one canonical serialized pack."""

        payload = _decode_json_object(value)
        if set(payload) != {"schema", "items", "digest", "byte_count"}:
            raise ContextPackError("pack fields do not match the wire schema")
        if payload["schema"] != _PACK_SCHEMA:
            raise ContextPackError("unsupported context pack schema")
        items = _decode_items(payload["items"])
        digest = payload["digest"]
        byte_count = payload["byte_count"]
        if not _valid_digest(digest):
            raise ContextPackError("invalid pack digest")
        if type(byte_count) is not int or byte_count < 0:
            raise ContextPackError("invalid pack byte_count")
        pack = cls.build(items)
        if pack.digest != digest:
            raise ContextPackError("pack digest verification failed")
        if pack.byte_count != byte_count:
            raise ContextPackError("pack byte_count verification failed")
        pack.to_bytes()
        return pack


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """A target pack represented by a retained base prefix and appended target suffix."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        """Build a delta using the longest identical item prefix."""

        _require_pack(base)
        _require_pack(target)
        prefix_items = 0
        for base_item, target_item in zip(base.items, target.items, strict=False):
            if base_item != target_item:
                break
            prefix_items += 1
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix_items,
            appended=target.items[prefix_items:],
        )

    @property
    def byte_count(self) -> int:
        """Return the exact canonical delta wire size."""

        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        """Serialize this delta in the canonical public JSON representation."""

        if not _valid_digest(self.base_digest) or not _valid_digest(self.target_digest):
            raise ContextPackError("invalid delta digest")
        if type(self.prefix_items) is not int or self.prefix_items < 0:
            raise ContextPackError("prefix_items must be a non-negative integer")
        appended = _validate_items(self.appended)
        return _canonical_json({
            "schema": _DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": list(_encode_items(appended)),
        })

    @classmethod
    def from_bytes(cls, value: bytes) -> ContextPackDelta:
        """Decode and validate one canonical serialized delta."""

        payload = _decode_json_object(value)
        if set(payload) != {"schema", "base_digest", "target_digest", "prefix_items", "appended"}:
            raise ContextPackError("delta fields do not match the wire schema")
        if payload["schema"] != _DELTA_SCHEMA:
            raise ContextPackError("unsupported context pack delta schema")
        base_digest = payload["base_digest"]
        target_digest = payload["target_digest"]
        prefix_items = payload["prefix_items"]
        if not _valid_digest(base_digest) or not _valid_digest(target_digest):
            raise ContextPackError("invalid delta digest")
        if type(prefix_items) is not int or prefix_items < 0:
            raise ContextPackError("prefix_items must be a non-negative integer")
        delta = cls(
            base_digest=base_digest,
            target_digest=target_digest,
            prefix_items=prefix_items,
            appended=_decode_items(payload["appended"]),
        )
        delta.to_bytes()
        return delta


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Validate and apply a serialized-independent delta to its exact base pack."""

    _require_pack(base)
    if not isinstance(delta, ContextPackDelta):
        raise ContextPackError("delta must be a ContextPackDelta")
    delta.to_bytes()
    if delta.base_digest != base.digest:
        raise ContextPackError("delta base digest does not match the supplied pack")
    if delta.prefix_items > len(base.items):
        raise ContextPackError("delta prefix exceeds the base pack")
    target = ContextPack.build((*base.items[: delta.prefix_items], *delta.appended))
    if target.digest != delta.target_digest:
        raise ContextPackError("delta target digest verification failed")
    return target


def _require_pack(value: object) -> ContextPack:
    if not isinstance(value, ContextPack):
        raise ContextPackError("base and target must be ContextPack values")
    value.to_bytes()
    return value


def _validate_items(value: object) -> tuple[bytes, ...]:
    if type(value) is not tuple:
        raise ContextPackError("pack items must be a bytes tuple")
    validated: list[bytes] = []
    for item in value:
        if type(item) is not bytes:
            raise ContextPackError("each pack item must be bytes")
        try:
            item.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ContextPackError("each pack item must be valid UTF-8") from error
        validated.append(item)
    return tuple(validated)


def _encode_items(items: tuple[bytes, ...]) -> tuple[str, ...]:
    return tuple(base64.b64encode(item).decode("ascii") for item in items)


def _decode_items(value: object) -> tuple[bytes, ...]:
    if type(value) is not list:
        raise ContextPackError("wire items must be a Base64 string array")
    decoded: list[bytes] = []
    for encoded in value:
        if type(encoded) is not str:
            raise ContextPackError("wire items must contain Base64 strings")
        try:
            raw = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (UnicodeEncodeError, binascii.Error, ValueError) as error:
            raise ContextPackError("invalid Base64 item") from error
        if base64.b64encode(raw).decode("ascii") != encoded:
            raise ContextPackError("Base64 item is not canonical")
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ContextPackError("decoded item must be valid UTF-8") from error
        decoded.append(raw)
    return tuple(decoded)


def _pack_digest(encoded_items: tuple[str, ...]) -> str:
    unsigned = _canonical_json({"schema": _PACK_SCHEMA, "items": list(encoded_items)})
    return "sha256:" + hashlib.sha256(unsigned).hexdigest()


def _pack_payload(encoded_items: tuple[str, ...], digest: str) -> tuple[int, bytes]:
    byte_count = 0
    for _attempt in range(10):
        payload = _canonical_json({
            "schema": _PACK_SCHEMA,
            "items": list(encoded_items),
            "digest": digest,
            "byte_count": byte_count,
        })
        actual = len(payload)
        if actual == byte_count:
            return actual, payload
        byte_count = actual
    raise ContextPackError("pack byte_count did not converge")


def _canonical_json(value: dict[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _decode_json_object(value: bytes) -> dict[str, Any]:
    if type(value) is not bytes:
        raise ContextPackError("wire value must be bytes")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ContextPackError("duplicate JSON field")
            result[key] = item
        return result

    try:
        decoded = value.decode("utf-8")
        payload = json.loads(decoded, object_pairs_hook=reject_duplicates)
    except ContextPackError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise ContextPackError("invalid context pack JSON") from error
    if type(payload) is not dict:
        raise ContextPackError("wire payload must be a JSON object")
    return payload


def _valid_digest(value: object) -> bool:
    return type(value) is str and _DIGEST_PATTERN.fullmatch(value) is not None


__all__ = [
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "apply_context_pack_delta",
]
