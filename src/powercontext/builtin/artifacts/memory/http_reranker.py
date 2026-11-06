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

"""HTTP-backed scoring for identity-preserving Memory reranking."""

from __future__ import annotations

from math import isfinite
from typing import Any

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage


class HTTPMemoryReranker:
    """Score bounded candidate batches and select from the complete candidate pool."""

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = "http-cross-encoder-v1",
    ) -> None:
        if type(batch_size) is not int or batch_size <= 0:
            raise ValueError("reranker batch_size must be a positive integer")  # noqa: TRY003
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Return globally sorted original ranks from all HTTP score batches."""

        candidate_count = len(candidates)
        if candidate_count < 1:
            raise ValueError("rerank requires at least one candidate")  # noqa: TRY003
        if type(limit) is not int or limit < 1 or limit > candidate_count:
            raise ValueError("rerank limit must be between one and the candidate count")  # noqa: TRY003

        scores: dict[int, float] = {}
        requests = 0
        ranked_candidates = tuple(enumerate(candidates, start=1))
        for offset in range(0, candidate_count, self._batch_size):
            batch = ranked_candidates[offset : offset + self._batch_size]
            expected_ranks = {rank for rank, _candidate in batch}
            response = await self._client.post(
                self._endpoint,
                json={
                    "query": query,
                    "candidates": [{"rank": rank, "text": candidate.text} for rank, candidate in batch],
                },
            )
            requests += 1
            response.raise_for_status()
            scores.update(_validated_scores(response.json(), expected_ranks=expected_ranks))

        selected_ranks = tuple(sorted(scores, key=lambda rank: (-scores[rank], rank))[:limit])
        return MemoryRerankDecision(
            selected_ranks=selected_ranks,
            usage=InferenceUsage(requests=requests),
        )


def _validated_scores(value: Any, *, expected_ranks: set[int]) -> dict[int, float]:
    if type(value) is not dict or set(value) != {"scores"}:
        raise ValueError("reranker response must contain only scores")  # noqa: TRY003

    records = value["scores"]
    if type(records) is not list:
        raise ValueError("reranker scores must be a JSON array")  # noqa: TRY003

    scores: dict[int, float] = {}
    for record in records:
        if type(record) is not dict or set(record) != {"rank", "score"}:
            raise ValueError("each reranker score must contain only rank and score")  # noqa: TRY003
        rank = record["rank"]
        score = record["score"]
        if type(rank) is not int or rank not in expected_ranks or rank in scores:
            raise ValueError("reranker scores must cover each batch rank exactly once")  # noqa: TRY003
        if type(score) not in {int, float} or not isfinite(score):
            raise ValueError("reranker score must be a finite number")  # noqa: TRY003
        scores[rank] = float(score)

    if set(scores) != expected_ranks:
        raise ValueError("reranker scores must cover each batch rank exactly once")  # noqa: TRY003
    return scores


__all__ = ["HTTPMemoryReranker"]
