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

"""Cross-call Embedding request coalescing with a bounded success cache."""

from __future__ import annotations

import asyncio
import math
from collections import OrderedDict
from dataclasses import dataclass, field

from powercontext.builtin.inference.models import EmbeddingResult, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel

_EmbeddingVector = tuple[float, ...]


@dataclass(slots=True)
class _Batch:
    texts: tuple[str, ...]
    waiters: tuple[_TextWaiter, ...]
    usage: InferenceUsage = field(default_factory=lambda: InferenceUsage(requests=0))


@dataclass(slots=True)
class _TextWaiter:
    future: asyncio.Future[_EmbeddingVector]
    batch: _Batch | None = None


class CoalescingEmbeddingModel:
    """Share in-flight text embeddings and cache successful vectors.

    Every mutation happens on the event loop before the first ``await`` of
    ``embed``, so check-then-register is atomic for a single-loop Runtime.  A
    waiter cancellation only cancels that caller's wait; the shared batch Task
    keeps running for every other waiter.
    """

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if type(max_batch_size) is not int or max_batch_size < 1:
            raise ValueError("max_batch_size must be a positive integer")  # noqa: TRY003
        if isinstance(coalesce_ms, bool) or not isinstance(coalesce_ms, int | float):
            raise ValueError("coalesce_ms must be a non-negative number")  # noqa: TRY003, TRY004
        if not math.isfinite(coalesce_ms) or coalesce_ms < 0:
            raise ValueError("coalesce_ms must be a non-negative finite number")  # noqa: TRY003
        if type(cache_size) is not int or cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")  # noqa: TRY003
        self._model = model
        self.profile = model.profile
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = float(coalesce_ms) / 1000.0
        self._cache_size = cache_size
        self._cache: OrderedDict[str, _EmbeddingVector] = OrderedDict()
        self._pending: dict[str, _TextWaiter] = {}
        self._inflight: dict[str, _TextWaiter] = {}
        self._drain_task: asyncio.Task[None] | None = None
        self._batch_tasks: set[asyncio.Task[None]] = set()

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:  # noqa: C901 - one ordered wait path
        """Embed one ordered batch, deduplicating within and across callers."""

        try:
            requested = tuple(texts)
        except TypeError as error:
            raise TypeError("texts must be an iterable of strings") from error  # noqa: TRY003
        if any(not isinstance(text, str) for text in requested):
            raise TypeError("texts must contain only strings")  # noqa: TRY003
        if not requested:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))

        unique: list[str] = []
        seen: set[str] = set()
        for text in requested:
            if text not in seen:
                seen.add(text)
                unique.append(text)

        direct: dict[str, _EmbeddingVector] = {}
        waiting: dict[str, _TextWaiter] = {}
        for text in unique:
            cached = self._cache.get(text)
            if cached is not None:
                self._cache.move_to_end(text)
                direct[text] = cached
                continue
            waiter = self._inflight.get(text)
            if waiter is None:
                waiter = self._pending.get(text)
            if waiter is None:
                waiter = _TextWaiter(future=asyncio.get_running_loop().create_future())
                waiter.future.add_done_callback(_consume_future_exception)
                self._pending[text] = waiter
                self._ensure_drain()
            waiting[text] = waiter

        if waiting:
            await asyncio.wait(tuple(waiter.future for waiter in waiting.values()))
            cancelled = False
            errors: list[BaseException] = []
            for text, waiter in waiting.items():
                if waiter.future.cancelled():
                    cancelled = True
                elif (error := waiter.future.exception()) is not None:
                    errors.append(error)
                else:
                    direct[text] = waiter.future.result()
            if cancelled:
                raise asyncio.CancelledError
            if errors:
                raise errors[0]

        batches: dict[int, _Batch] = {}
        for text in waiting:
            batch = waiting[text].batch
            if batch is not None:
                batches[id(batch)] = batch
        usage = _aggregate_usage(tuple(batches.values()))
        return EmbeddingResult(vectors=tuple(direct[text] for text in requested), usage=usage)

    def _ensure_drain(self) -> None:
        if self._drain_task is None or self._drain_task.done():
            self._drain_task = asyncio.create_task(self._drain(), name="embedding-coalescer")

    async def _drain(self) -> None:
        try:
            if self._coalesce_seconds > 0:
                await asyncio.sleep(self._coalesce_seconds)
            else:
                # One event-loop turn lets callers that arrive together in the
                # same tick enqueue before the first batch is cut.
                await asyncio.sleep(0)
            while self._pending:
                items = tuple(self._pending.items())[: self._max_batch_size]
                for text, _waiter in items:
                    del self._pending[text]
                batch = _Batch(
                    texts=tuple(text for text, _waiter in items),
                    waiters=tuple(waiter for _text, waiter in items),
                )
                for _text, waiter in items:
                    waiter.batch = batch
                    self._inflight[_text] = waiter
                task = asyncio.create_task(self._run_batch(batch), name="embedding-batch")
                self._batch_tasks.add(task)
                task.add_done_callback(self._batch_tasks.discard)
        finally:
            self._drain_task = None
            if self._pending:
                self._ensure_drain()

    async def _run_batch(self, batch: _Batch) -> None:  # noqa: C901 - cache and waiter fan-out
        try:
            result = await self._model.embed(batch.texts)
            if len(result.vectors) != len(batch.texts):
                raise ValueError("embedding provider returned the wrong vector count")  # noqa: TRY003, TRY301
            batch.usage = result.usage
            if self._cache_size > 0:
                for text, vector in zip(batch.texts, result.vectors, strict=True):
                    self._cache[text] = vector
                    self._cache.move_to_end(text)
                    while len(self._cache) > self._cache_size:
                        self._cache.popitem(last=False)
            for waiter, vector in zip(batch.waiters, result.vectors, strict=True):
                if not waiter.future.done():
                    waiter.future.set_result(vector)
        except BaseException as error:
            for waiter in batch.waiters:
                if not waiter.future.done():
                    waiter.future.set_exception(error)
        finally:
            for text in batch.texts:
                self._inflight.pop(text, None)


def _aggregate_usage(batches: tuple[_Batch, ...]) -> InferenceUsage:
    requests = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    for batch in batches:
        usage = batch.usage
        requests += usage.requests
        if usage.input_tokens is not None:
            input_tokens = (0 if input_tokens is None else input_tokens) + usage.input_tokens
        if usage.output_tokens is not None:
            output_tokens = (0 if output_tokens is None else output_tokens) + usage.output_tokens
    return InferenceUsage(requests=requests, input_tokens=input_tokens, output_tokens=output_tokens)


def _consume_future_exception(future: asyncio.Future[_EmbeddingVector]) -> None:
    if future.cancelled():
        return
    future.exception()


__all__ = ["CoalescingEmbeddingModel"]
