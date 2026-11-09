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

import pytest

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    SubmitSourceObservation,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import SourceDefinitionRegistry, manifest_for_definition, project_source_for_transport
from powercontext.sources.pdf_pages import PDF_PAGE_SOURCE_DEFINITION, PDFPageInput, search_pdf_pages
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY


class _PagePipeline:
    async def extract(self, request, /):
        return tuple(
            MemoryEntryInput(
                kind="source-evidence",
                text=source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"],
                sources=(source,),
            )
            for source in request.sources
        )


async def _ingest(runtime, scope_id, pages) -> None:
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    for page in pages:
        source = await registry.resolve(page)
        await runtime.ingestion.submit(
            SubmitSourceObservation(scope_id=scope_id, observation=project_source_for_transport(registry, source))
        )
    memory = runtime.memory.for_scope(scope_id)
    while (await memory.flush()).processed:
        pass


def test_statement_evidence_survives_many_title_mentions_and_runtime_restart(tmp_path) -> None:
    async def scenario() -> None:
        document_id = "NORTH_WIND_2024_10K"
        pages = [
            PDFPageInput(
                document_id=document_id,
                document_sha256="a" * 64,
                page_number=index + 1,
                text=f"Exhibit {index}\nOur auditor examined the consolidated statements of income and "
                "the consolidated statements of cash flows. Refer to those statements of "
                "income and cash flows for operating income and depreciation and amortization.",
            )
            for index in range(35)
        ]
        pages.extend((
            PDFPageInput(
                document_id=document_id,
                document_sha256="a" * 64,
                page_number=40,
                text="CONSOLIDATED STATEMENTS OF OPERATIONS\n2024 2023\n"
                "Revenue 9000 8000\nOperating income 1200 1100\nNet income 1000 900",
            ),
            PDFPageInput(
                document_id=document_id,
                document_sha256="a" * 64,
                page_number=41,
                text="CONSOLIDATED STATEMENT OF CASH FLOWS\n2024 2023\n"
                "Net income 1000 900\nDepreciation and amortization 200 180\nCapital expenditures 300 250",
            ),
        ))
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'pages.db'}"))
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            scope = await runtime.scopes.create(
                ScopeDraft(title="Reports", summary="Evidence", idempotency_key="reports")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            await _ingest(runtime, scope.scope_id, pages)
            await _ingest(runtime, scope.scope_id, (pages[0],))

        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            question = "What is North Wind's FY2024 EBITDA less capex using operating income and D&A?"
            citations = await search_pdf_pages(memory, question, limit=20)
            locations = []
            for citation in citations:
                record = await memory.get(GetMemoryEntryRequest(citation=citation))
                locations.append(record.entry.sources[0].source_id)
            expected = [
                (await SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,)).resolve(page)).name
                for page in pages[-2:]
            ]
            assert set(expected).issubset(locations)
            assert len(locations) == len(set(locations)) <= 20
            assert len(await search_pdf_pages(memory, question, limit=1)) <= 1
            assert len((await memory.list()).entries) == len(pages)

    asyncio.run(scenario())


def test_description_split_across_pages_keeps_its_continuation() -> None:
    async def scenario() -> None:
        pages = (
            PDFPageInput(
                document_id="EXAMPLE_2024_10K",
                document_sha256="b" * 64,
                page_number=1,
                text="Our business\nOur reportable segments cover these geographic regions:\n"
                "1) Western districts, including several coastal territories;",
            ),
            PDFPageInput(
                document_id="EXAMPLE_2024_10K",
                document_sha256="b" * 64,
                page_number=2,
                text="2) Eastern districts, including the inland territories; and\n3) Northern districts.",
            ),
        )
        async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=_PagePipeline()) as runtime:
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            scope = await runtime.scopes.create(
                ScopeDraft(title="Regions", summary="Regions", idempotency_key="regions")
            )
            await _ingest(runtime, scope.scope_id, pages)
            memory = runtime.memory.for_scope(scope.scope_id)
            citations = await search_pdf_pages(memory, "What geographic regions does Example operate in?", limit=20)
            texts = [(await memory.get(GetMemoryEntryRequest(citation=c))).entry.text for c in citations]
            assert len(texts) == 2
            assert any("Eastern districts" in text for text in texts)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "question,limit",
    [("", 20), (" \n", 20), (None, 20), ("cash flow", 0), ("cash flow", -1), ("cash flow", True), ("cash flow", 1.5)],
)
def test_search_rejects_invalid_question_or_limit(question, limit) -> None:
    with pytest.raises(ValueError):
        asyncio.run(search_pdf_pages(None, question, limit=limit))


def test_multiword_company_routing_and_scope_isolation() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=_PagePipeline()) as runtime:
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            scope = await runtime.scopes.create(
                ScopeDraft(title="Reports", summary="Reports", idempotency_key="reports")
            )
            other = await runtime.scopes.create(ScopeDraft(title="Other", summary="Other", idempotency_key="other"))
            pages = [
                PDFPageInput(
                    document_id=company,
                    document_sha256="a" * 64,
                    page_number=index + 1,
                    text="CONSOLIDATED STATEMENTS OF FINANCIAL POSITION\n2024\n"
                    "Current assets 500\nCurrent liabilities 250\nCash and cash equivalents 100",
                )
                for index, company in enumerate(("NORTH_STAR_2024_10K", "NORTH_WIND_2024_10K"))
            ]
            await _ingest(runtime, scope.scope_id, pages)
            await _ingest(runtime, other.scope_id, (pages[0],))
            memory = runtime.memory.for_scope(scope.scope_id)
            citations = await search_pdf_pages(memory, "What is North Wind's 2024 current ratio?", limit=20)
            assert citations
            for citation in citations:
                record = await memory.get(GetMemoryEntryRequest(citation=citation))
                assert record.entry.text.startswith("NORTH_WIND_2024_10K\n")
            isolated = runtime.memory.for_scope(other.scope_id)
            for citation in await search_pdf_pages(isolated, "current ratio", limit=20):
                record = await isolated.get(GetMemoryEntryRequest(citation=citation))
                assert record.entry.text.startswith("NORTH_STAR")

    asyncio.run(scenario())
