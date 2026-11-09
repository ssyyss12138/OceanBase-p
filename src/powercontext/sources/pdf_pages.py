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

"""Captured PDF pages with physical page identities."""

from __future__ import annotations

import hashlib
import io
import math
import re
from collections import Counter
from itertools import pairwise
from types import SimpleNamespace
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, StrictInt, model_validator
from pypdf import PdfReader

from powercontext.artifacts import MemoryCitation
from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PDF_PAGE_SOURCE_NAME = "pdf-page"


class PDFDocumentInput(BaseModel):
    """One complete offline PDF document."""

    document_id: str
    content: bytes


class PDFPageInput(BaseModel):
    """One physical PDF page with its document revision identity."""

    document_id: str
    document_sha256: str
    page_number: StrictInt
    text: str

    @model_validator(mode="after")
    def validate_page(self) -> PDFPageInput:
        _validate_document_id(self.document_id)
        _validate_document_sha256(self.document_sha256)
        if type(self.page_number) is not int or self.page_number < 1:
            raise ValueError("page_number must be a positive integer")  # noqa: TRY003
        return self


class PDFPageSource(Source):
    """A captured physical page addressable by document revision and page number."""

    materialization: Literal[SourceMaterialization.CAPTURED] = SourceMaterialization.CAPTURED
    source_id: str
    document_id: str
    document_sha256: str
    page_number: StrictInt
    text: str

    @model_validator(mode="after")
    def validate_identity_and_page(self) -> PDFPageSource:
        page = PDFPageInput(
            document_id=self.document_id,
            document_sha256=self.document_sha256,
            page_number=self.page_number,
            text=self.text,
        )
        expected_id = pdf_page_source_id(page.document_id, page.document_sha256, page.page_number)
        if self.name != expected_id or self.source_id != expected_id:
            raise ValueError("name and source_id must match the PDF page identity")  # noqa: TRY003
        return self


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Parse every physical page of a complete PDF atomically."""

    _validate_document_id(value.document_id)
    if not value.content:
        raise ValueError("PDF content must not be empty")  # noqa: TRY003
    sha256 = hashlib.sha256(value.content).hexdigest()
    try:
        reader = PdfReader(io.BytesIO(value.content))
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise ValueError("PDF requires a user password")  # noqa: TRY003, TRY301
        pages = tuple(
            PDFPageInput(
                document_id=value.document_id,
                document_sha256=sha256,
                page_number=page_number,
                text=page.extract_text() or "",
            )
            for page_number, page in enumerate(reader.pages, start=1)
        )
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("PDF could not be parsed completely") from error  # noqa: TRY003
    return pages


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Return the bounded, collision-resistant identity for one physical PDF page."""

    _validate_document_id(document_id)
    _validate_document_sha256(document_sha256)
    if type(page_number) is not int or page_number < 1:
        raise ValueError("page_number must be a positive integer")  # noqa: TRY003
    encoded = quote(document_id, safe="")
    source_id = f"pdf:{encoded}:{document_sha256}:{page_number}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        digest = hashlib.sha256(document_id.encode("utf-8")).hexdigest()
        source_id = f"pdf:{digest}:{document_sha256}:{page_number}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError("PDF page source_id exceeds the supported length")  # noqa: TRY003
    return source_id


class PDFPageSourceAdapter:
    """Resolve and read captured PDF page Sources."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        source_id = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=source_id,
            source_id=source_id,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Expose a PDF page as document-id-prefixed page text."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.source_id,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


_PDF_TOKEN = re.compile(r"[a-z]+|\d+")
_PDF_STOPWORDS = frozenset(
    ["a", "about", "above", "after", "again", "against", "all", "also", "am", "an", "and", "any", "are", "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but", "by", "can", "could", "did", "do", "does", "doing", "down", "during", "each", "few", "for", "from", "further", "had", "has", "have", "having", "he", "her", "here", "hers", "herself", "him", "himself", "his", "how", "i", "if", "in", "into", "is", "it", "its", "itself", "just", "me", "more", "most", "my", "myself", "no", "nor", "not", "now", "of", "off", "on", "once", "only", "or", "other", "our", "ours", "ourselves", "out", "over", "own", "same", "she", "should", "so", "some", "such", "than", "that", "the", "their", "theirs", "them", "themselves", "then", "there", "these", "they", "this", "those", "through", "to", "too", "under", "until", "up", "very", "was", "we", "were", "what", "when", "where", "which", "while", "who", "whom", "why", "will", "with", "would", "you", "your", "yours", "yourself", "yourselves"]
)
_PDF_PRIORITY_QUERIES = frozenset(
    {
        "income statement",
        "statement of cash flows",
        "operating income",
        "depreciation amortization",
        "capital expenditures",
        "litigation",
        "restructuring",
        "operating margin",
        "gross margin",
        "quick ratio",
        "capital intensive",
        "segment growth",
        "acquisitions divestitures",
        "free cash flow",
        "consolidated statement of income",
        "consolidated statements of income",
        "consolidated balance sheet",
        "consolidated balance sheets",
        "consolidated statements of cash flows",
        "geographic operations",
    }
)
_PDF_STRUCTURAL_QUERIES = frozenset(
    {
        "consolidated statement of income",
        "consolidated statements of income",
        "consolidated balance sheet",
        "consolidated balance sheets",
        "consolidated statements of cash flows",
        "geographic operations",
    }
)
_PDF_INSTRUCTION_TERMS = frozenset(
    ["answer", "assume", "assuming", "based", "calculate", "calculation", "define", "defined", "details", "directly", "explicitly", "give", "outlined", "perspective", "question", "relying", "respond", "shown", "state", "statement", "use", "using", "within"]
)


def _pdf_tokens(text: str) -> list[str]:
    return _PDF_TOKEN.findall(text.lower())


def _pdf_content_terms(question: str) -> tuple[str, ...]:  # noqa: C901 - bounded term expansion
    terms: list[str] = []
    seen: set[str] = set()
    for term in _pdf_tokens(question):
        if term in _PDF_STOPWORDS or term in _PDF_INSTRUCTION_TERMS or len(term) < 2:
            continue
        if term not in seen:
            seen.add(term)
            terms.append(term)
    lowered = question.casefold().replace("-", " ")
    expansions: list[str] = []
    if "d&a" in lowered or "d & a" in lowered:
        expansions.extend(("depreciation", "amortization"))
    if "ebitda" in lowered:
        expansions.extend(("operating", "income", "depreciation", "amortization"))
    if "capex" in lowered or "capital intensive" in lowered:
        expansions.extend(("capital", "expenditures"))
    if "m&a" in lowered or "m & a" in lowered:
        expansions.extend(("acquisitions", "divestitures"))
    if "p&l" in lowered or "p & l" in lowered:
        expansions.extend(("income", "statement"))
    if "legal" in lowered:
        expansions.append("litigation")
    if "geograph" in lowered:
        expansions.extend(("geographic", "geography"))
    for term in expansions:
        if term not in seen:
            seen.add(term)
            terms.append(term)
    for term in tuple(terms):
        if term.startswith("fy") and term[2:].isdigit():
            year = term[2:]
            if year not in seen:
                seen.add(year)
                terms.append(year)
    return tuple(terms)


def _pdf_query_variants(question: str) -> tuple[str, ...]:  # noqa: C901 - bounded variants
    terms = _pdf_content_terms(question)
    variants: list[str] = [question]
    lowered = question.casefold().replace("-", " ")
    if "d&a" in lowered or "d & a" in lowered:
        variants.append("depreciation amortization")
    if "ebitda" in lowered:
        variants.extend(("operating income", "depreciation amortization"))
    if "capex" in lowered or "capital intensive" in lowered:
        variants.append("capital expenditures")
    if "m&a" in lowered or "m & a" in lowered:
        variants.append("acquisitions divestitures")
    if "p&l" in lowered or "p & l" in lowered:
        variants.append("income statement")
    if "legal" in lowered:
        variants.append("litigation")
    if "restructur" in lowered:
        variants.append("restructuring")
    if "cash flow" in lowered:
        variants.append("statement of cash flows")
    if any(
        marker in lowered
        for marker in (
            "income", "margin", "revenue", "ebitda", "profit", "earnings", "operating", "sales", "gross", "capital intensive"
        )
    ):
        variants.append("consolidated statements of income")
    if any(
        marker in lowered
        for marker in ("liquidity", "quick ratio", "current ratio", "balance sheet", "assets", "liabilities", "debt", "capital intensive")
    ):
        variants.append("consolidated balance sheets")
    if any(
        marker in lowered
        for marker in ("cash flow", "capex", "capital expenditure", "depreciation", "amortization", "capital intensive")
    ):
        variants.append("consolidated statements of cash flows")
    if any(marker in lowered for marker in ("geograph", "segment", "region", "country", "countries")):
        variants.extend(("geographic operations", "segment"))
    if terms:
        variants.append(" ".join(terms))
        ranked = sorted(terms, key=lambda term: (-len(term), term))
        variants.extend(ranked[:10])
        original = _pdf_tokens(question)
        positions = {term: index for index, term in reversed(list(enumerate(original)))}
        for left, right in pairwise(terms):
            if left in positions and right in positions and positions[right] > positions[left]:
                variants.append(f"{left} {right}")
        for term in terms:
            if term.startswith("fy") and term[2:].isdigit():
                variants.append(term[2:])
    return tuple(dict.fromkeys(variant for variant in variants if variant.strip()))


def _pdf_statement_phrases(question: str) -> tuple[str, ...]:
    lowered = question.casefold().replace("-", " ")
    phrases: list[str] = []

    def add(*values: str) -> None:
        for value in values:
            if value not in phrases:
                phrases.append(value)

    income = any(
        marker in lowered
        for marker in (
            "income", "margin", "revenue", "ebitda", "profit", "earnings", "operating", "sales", "gross", "capital intensive"
        )
    )
    balance = any(
        marker in lowered
        for marker in ("liquidity", "quick ratio", "current ratio", "balance sheet", "assets", "liabilities", "debt", "capital intensive")
    )
    cash = any(
        marker in lowered
        for marker in ("cash flow", "capex", "capital expenditure", "depreciation", "amortization", "capital intensive")
    )
    geographic = any(marker in lowered for marker in ("geograph", "segment", "region", "country", "countries"))
    if income:
        add("consolidated statements of income", "consolidated statement of income", "statement of income")
    if balance:
        add("consolidated balance sheets", "consolidated balance sheet", "balance sheet")
    if cash:
        add("consolidated statements of cash flows", "consolidated statement of cash flows")
    if geographic:
        add("geographic operations", "segment")
    return tuple(phrases)


def _pdf_rank(hits, question: str, bonus: dict[tuple[str, int, str, str], float] | None = None):
    terms = _pdf_content_terms(question)
    unique_terms = tuple(dict.fromkeys(terms))
    documents = [(hit, _pdf_tokens(hit.text)) for hit in hits]
    document_count = max(1, len(documents))
    frequency = Counter(term for _hit, tokens in documents for term in set(tokens))
    average_length = max(1.0, sum(len(tokens) for _hit, tokens in documents) / document_count)
    query_set = set(unique_terms)
    phrase_pairs = set(pairwise(unique_terms))
    statement_phrases = _pdf_statement_phrases(question)
    scored = []
    for hit, tokens in documents:
        key = (hit.memory_ref.artifact_id, hit.memory_ref.revision, hit.entry_id, hit.entry_version_id)
        counts = Counter(tokens)
        score = 0.0
        for term in query_set:
            tf = counts.get(term, 0)
            if not tf:
                continue
            inverse = math.log((document_count + 1) / (frequency.get(term, 0) + 0.5)) + 1.0
            score += inverse * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * len(tokens) / average_length))
        matched = len(query_set.intersection(tokens))
        score += 2.0 * matched / max(1, len(query_set))
        heading = hit.text[:1500].lower()
        score += 0.35 * sum(1 for term in query_set if term in heading)
        score += 20.0 * sum(1 for phrase in statement_phrases if phrase in heading)
        for left, right in phrase_pairs:
            if f"{left} {right}" in hit.text.lower():
                score += 0.75
        score += 0.0 if bonus is None else bonus.get(key, 0.0)
        scored.append((score, hit))
    scored.sort(key=lambda item: (-item[0], item[1].entry_id, item[1].entry_version_id))
    return tuple(hit for _score, hit in scored)


def _pdf_is_priority(variant: str) -> bool:
    return variant in _PDF_PRIORITY_QUERIES


def _pdf_document_id(hit) -> str:
    return hit.text.split("\n", 1)[0].strip()


def _pdf_document_targets(question: str, documents: set[str]) -> set[str]:
    compact_question = re.sub(r"[^a-z0-9]", "", question.casefold())
    scores: dict[str, int] = {}
    for document_id in documents:
        parts = document_id.casefold().split("_")
        company = re.sub(r"[^a-z0-9]", "", parts[0]) if parts else ""
        score = 0
        if len(company) >= 2 and company in compact_question:
            score += 100
        for part in parts[1:]:
            if part.isdigit() and part in compact_question:
                score += 1
        scores[document_id] = score
    best = max(scores.values(), default=0)
    if best < 100:
        return set()
    return {document_id for document_id, score in scores.items() if score == best}


async def _pdf_list_candidates(memory) -> tuple[object, ...]:
    """Read every active page through the public Memory listing API."""

    list_entries = getattr(memory, "list", None)
    if list_entries is None:
        return ()
    try:
        page = await list_entries()
    except Exception:
        return ()
    candidates = []
    for record in getattr(page, "entries", ()):
        entry = getattr(record, "entry", None)
        citation = getattr(record, "citation", None)
        if entry is None or citation is None:
            continue
        source_ids = tuple(
            reference.source_id
            for reference in getattr(entry, "sources", ())
            if reference.source_type == PDF_PAGE_SOURCE_NAME
        )
        if not source_ids:
            continue
        candidates.append(
            SimpleNamespace(
                memory_ref=citation.memory_ref,
                entry_id=citation.entry_id,
                entry_version_id=citation.entry_version_id,
                text=entry.text,
                source_id=source_ids[0],
                citation=citation,
            )
        )
    return tuple(candidates)


async def search_pdf_pages(  # noqa: C901 - bounded multi-query retrieval
    memory,
    question: str,
    limit: int = 10,
) -> tuple[MemoryCitation, ...]:
    """Return exact citations for up to ``limit`` distinct physical pages."""

    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must not be empty or blank")  # noqa: TRY003
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")  # noqa: TRY003
    from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest

    listed = await _pdf_list_candidates(memory)

    candidates: dict[tuple[str, int, str, str], object] = {}
    bonus: dict[tuple[str, int, str, str], float] = {}
    priority_hits: list[object] = []
    structural_results: list[tuple[str, tuple[object, ...]]] = []
    full_hits: tuple[object, ...] = ()
    candidate_limit = max(limit * 4, 80)
    for variant in _pdf_query_variants(question):
        variant_limit = max(candidate_limit, 500) if _pdf_is_priority(variant) else candidate_limit
        page = await memory.search(SearchMemoryRequest(query=variant, mode="fts", limit=variant_limit))
        if variant == question:
            full_hits = page.hits
        if variant in _PDF_STRUCTURAL_QUERIES:
            structural_results.append((variant, page.hits))
        for rank, hit in enumerate(page.hits, start=1):
            key = (
                hit.memory_ref.artifact_id,
                hit.memory_ref.revision,
                hit.entry_id,
                hit.entry_version_id,
            )
            candidates.setdefault(key, hit)
            if _pdf_is_priority(variant):
                bonus[key] = max(bonus.get(key, 0.0), 12.0 / rank)
                if rank <= 20:
                    priority_hits.append(hit)
    for candidate in listed:
        key = (
            candidate.memory_ref.artifact_id,
            candidate.memory_ref.revision,
            candidate.entry_id,
            candidate.entry_version_id,
        )
        candidates.setdefault(key, candidate)
    if not candidates:
        return ()
    documents = {_pdf_document_id(hit) for hit in candidates.values()}
    targets = _pdf_document_targets(question, documents)
    if not targets:
        counts = Counter(_pdf_document_id(hit) for hit in full_hits)
        top_count = max(counts.values(), default=0)
        if top_count >= 2:
            targets = {document_id for document_id, count in counts.items() if count == top_count}
    if targets:
        candidates = {
            key: hit for key, hit in candidates.items() if _pdf_document_id(hit) in targets
        }
        priority_hits = [hit for hit in priority_hits if _pdf_document_id(hit) in targets]
        structural_results = [
            (variant, tuple(hit for hit in hits if _pdf_document_id(hit) in targets))
            for variant, hits in structural_results
        ]
    if not candidates:
        return ()
    ranked = _pdf_rank(tuple(candidates.values()), question, bonus)
    ordered: list[object] = []
    seen_keys: set[tuple[str, int, str, str]] = set()

    def add_hit(hit: object) -> None:
        key = (
            hit.memory_ref.artifact_id,
            hit.memory_ref.revision,
            hit.entry_id,
            hit.entry_version_id,
        )
        if key not in seen_keys:
            seen_keys.add(key)
            ordered.append(hit)

    for _variant, hits in structural_results:
        for hit in hits:
            if any(phrase in hit.text[:1500].casefold() for phrase in _PDF_STRUCTURAL_QUERIES):
                add_hit(hit)
    for slot in range(8):
        for _variant, hits in structural_results:
            if slot < len(hits):
                add_hit(hits[slot])
    for hit in priority_hits:
        add_hit(hit)
    for hit in ranked:
        add_hit(hit)
    citations: list[MemoryCitation] = []
    seen_pages: set[str] = set()
    for hit in ordered:
        citation = MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )
        record = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
        page_ids = tuple(
            reference.source_id for reference in record.sources if reference.source_type == PDF_PAGE_SOURCE_NAME
        )
        if not page_ids:
            continue
        page_id = page_ids[0]
        if page_id in seen_pages:
            continue
        seen_pages.add(page_id)
        citations.append(citation)
        if len(citations) >= limit:
            break
    return tuple(citations)

def _validate_document_id(document_id: str) -> None:
    if not document_id or not document_id.strip():
        raise ValueError("document_id must not be empty or blank")  # noqa: TRY003


def _validate_document_sha256(document_sha256: str) -> None:
    if len(document_sha256) != 64 or any(character not in "0123456789abcdef" for character in document_sha256):
        raise ValueError("document_sha256 must be a lowercase SHA-256 hex digest")  # noqa: TRY003


PDF_PAGE_SOURCE_ADAPTER = PDFPageSourceAdapter()
PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    PDF_PAGE_SOURCE_ADAPTER,
    projections=(PDFPageTextEvidenceProjection(),),
)


__all__ = [
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "parse_pdf_document",
    "pdf_page_source_id",
]
