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
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal
from urllib.parse import quote, unquote

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
        reader = PdfReader(io.BytesIO(value.content), strict=True)
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise ValueError("PDF requires a user password")  # noqa: TRY003, TRY301
        pages = []
        for page_number, page in enumerate(reader.pages, start=1):
            # extract_text may return early for a page without resources. Force
            # resolution of its contents first so broken references do not turn
            # into apparently valid blank pages.
            page.get_contents()
            pages.append(
                PDFPageInput(
                    document_id=value.document_id,
                    document_sha256=sha256,
                    page_number=page_number,
                    text=page.extract_text() or "",
                )
            )
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("PDF could not be parsed completely") from error  # noqa: TRY003
    return tuple(pages)


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
        # The additional colon separates hashed identities from the escaped
        # literal-ID namespace, including a literal ID equal to this digest.
        source_id = f"pdf:sha256:{digest}:{document_sha256}:{page_number}"
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
_PDF_STOPWORDS = frozenset([
    "a",
    "about",
    "above",
    "after",
    "again",
    "against",
    "all",
    "also",
    "am",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "because",
    "been",
    "before",
    "being",
    "below",
    "between",
    "both",
    "but",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "doing",
    "down",
    "during",
    "each",
    "few",
    "for",
    "from",
    "further",
    "had",
    "has",
    "have",
    "having",
    "he",
    "her",
    "here",
    "hers",
    "herself",
    "him",
    "himself",
    "his",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "itself",
    "just",
    "me",
    "more",
    "most",
    "my",
    "myself",
    "no",
    "nor",
    "not",
    "now",
    "of",
    "off",
    "on",
    "once",
    "only",
    "or",
    "other",
    "our",
    "ours",
    "ourselves",
    "out",
    "over",
    "own",
    "same",
    "she",
    "should",
    "so",
    "some",
    "such",
    "than",
    "that",
    "the",
    "their",
    "theirs",
    "them",
    "themselves",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "to",
    "too",
    "under",
    "until",
    "up",
    "very",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "whom",
    "why",
    "will",
    "with",
    "would",
    "you",
    "your",
    "yours",
    "yourself",
    "yourselves",
    "answer",
    "assume",
    "assuming",
    "based",
    "calculate",
    "calculation",
    "define",
    "defined",
    "details",
    "directly",
    "explicitly",
    "give",
    "outlined",
    "perspective",
    "question",
    "relying",
    "respond",
    "response",
    "shown",
    "state",
    "use",
    "using",
    "within",
    "analyst",
    "investment",
    "usd",
    "million",
    "millions",
    "fiscal",
    "fy",
    "reported",
    "report",
    "annual",
    "quantity",
])
_PDF_TERM_ALIASES = {
    "operating": "operation",
    "operations": "operation",
    "operates": "operation",
    "geographies": "geographic",
    "geographical": "geographic",
    "geography": "geographic",
    "countries": "country",
    "revenues": "revenue",
    "sales": "revenue",
    "expenditures": "expenditure",
    "spending": "expenditure",
    "amortisation": "amortization",
    "restructurings": "restructuring",
    "businesses": "business",
    "liabilities": "liability",
    "inventories": "inventory",
}
_PDF_CONCEPTS = (
    (r"\bd\s*&\s*a\b|\bdepreciation\b|\bamorti[sz]ation\b|\bebitda\b", "depreciation amortization"),
    (r"\bcapex\b|\bcapital\s+(?:intensive|expenditures?|spending)\b", "capital expenditure property plant equipment"),
    (r"\bm\s*&\s*a\b|\bacquisitions?\b|\bdivestitures?\b", "acquisition divestiture business combination"),
    (r"\blegal\b|\blitigation\b|\blawsuits?\b", "legal litigation lawsuit proceeding contingency settlement"),
    (
        r"\bgeograph\w*\b|\bregions?\b|\bcountries\b",
        "geographic country international worldwide segment division operation business",
    ),
    (r"\bquick\s+ratio\b", "cash equivalent receivable current liability inventory"),
    (r"\bcurrent\s+ratio\b|\bliquidity\b", "current asset liability cash"),
    (r"\bgross\s+(?:profit|margin)\b", "revenue cost sale"),
    (r"\bnet\s+(?:profit|margin)\b", "net income revenue"),
    (r"\brestructur\w*\b", "restructuring charge cost"),
    (r"\bdividends?\b", "dividend shareholder equity"),
    (r"\brepurchas\w*\b|\bbuybacks?\b", "repurchase treasury share stock"),
)
# A title must stand on its own near the top of a page. A sentence in an
# auditor's opinion or an index mentioning these words is not a statement.
_PDF_STATEMENT_TITLES = {
    "income": re.compile(
        r"^(?:(?:consolidated|combined|condensed|unaudited)\s+)*"
        r"(?:statements? of (?:income|operations|earnings|profit (?:and|or) loss)"
        r"|(?:income|earnings) statements?)(?:\s*\(.*\))?$"
    ),
    "balance": re.compile(
        r"^(?:(?:consolidated|combined|condensed|unaudited)\s+)*"
        r"(?:balance sheets?|statements? of financial position)(?:\s*\(.*\))?$"
    ),
    "cash": re.compile(
        r"^(?:(?:consolidated|combined|condensed|unaudited)\s+)*"
        r"statements? of cash flows?(?:\s*\(.*\))?$"
    ),
}


@dataclass(frozen=True)
class _PDFCandidate:
    citation: MemoryCitation
    text: str
    source_id: str
    document_id: str


def _pdf_tokens(text: str) -> tuple[str, ...]:
    tokens = []
    for token in _PDF_TOKEN.findall(text.lower()):
        if token in _PDF_TERM_ALIASES:
            token = _PDF_TERM_ALIASES[token]
        elif len(token) > 4 and token.endswith("ies"):
            token = token[:-3] + "y"
        elif len(token) > 4 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
            token = token[:-1]
        tokens.append(token)
    return tuple(tokens)


def _pdf_query(question: str) -> tuple[dict[str, float], tuple[str, ...]]:
    weights = dict.fromkeys(
        (
            t
            for t in _pdf_tokens(question)
            if t not in _PDF_STOPWORDS and t not in {"primarily", "mainly"} and not t.isdigit() and len(t) > 1
        ),
        1.0,
    )
    expansions = []
    normalized_question = question.lower().replace("-", " ")
    for pattern, expansion in _PDF_CONCEPTS:
        if re.search(pattern, normalized_question):
            expansions.append(expansion)
            for term in _pdf_tokens(expansion):
                weights.setdefault(term, 0.4)
    return weights, tuple(expansions)


def _pdf_statement_intents(question: str) -> tuple[str, ...]:
    text = question.lower().replace("-", " ")
    kinds = []
    if re.search(
        r"\bincome\b|\bp\s*&\s*l\b|\bebitda\b|\bmargin\b|\bprofit\b|\brevenue\b|\bsales\b|\breturn on\b|\bcapital intensive\b",
        text,
    ):
        kinds.append("income")
    if re.search(
        r"\bbalance\s+sheet|\bratio\b|\bliquidity\b|\bassets?\b|\bliabilit|\bcapital intensive\b|\bworking capital\b|\bdebt\b",
        text,
    ):
        kinds.append("balance")
    if re.search(
        r"\bcash\s+flow|\bcapex\b|\bebitda\b|\bd\s*&\s*a\b|\bdepreciation\b|\bamorti[sz]ation\b|\bcapital\s+(?:intensive|expenditure|spending)",
        text,
    ):
        kinds.append("cash")
    return tuple(kinds)


def _pdf_statement_kind(text: str) -> str | None:
    lines = [" ".join(line.lower().split()).strip() for line in text[:1500].splitlines()[:18]]
    for index, line in enumerate(lines):
        options = [line]
        if index + 1 < len(lines) and re.fullmatch(
            r"(?:(?:consolidated|combined|condensed|unaudited)\s*)+(?:statements?(?: of)?)?", line
        ):
            options.append(line + " " + lines[index + 1])
        for value in options:
            for kind, pattern in _PDF_STATEMENT_TITLES.items():
                if pattern.fullmatch(value):
                    return kind
    return None


def _pdf_document_targets(question: str, documents: set[str]) -> set[str]:
    compact_question = "".join(_PDF_TOKEN.findall(question.lower()))
    query_years = set(re.findall(r"(?:19|20)\d{2}", question))
    scores = {}
    for document_id in documents:
        company = re.split(r"(?:19|20)\d{2}", document_id.lower(), maxsplit=1)[0]
        company_terms = [
            t for t in _PDF_TOKEN.findall(company) if t not in {"inc", "corp", "corporation", "co", "ltd", "plc"}
        ]
        company = "".join(company_terms)
        if len(company) < 2 or company not in compact_question:
            continue
        years = set(re.findall(r"(?:19|20)\d{2}", document_id))
        scores[document_id] = (len(company), bool(years & query_years))
    if not scores:
        return set()
    best = max(scores.values())
    return {document_id for document_id, score in scores.items() if score == best}


def _pdf_candidate(record) -> _PDFCandidate | None:
    entry = record.entry
    source_ids = {ref.source_id for ref in entry.sources if ref.source_type == PDF_PAGE_SOURCE_NAME}
    if len(source_ids) != 1:
        return None
    source_id = next(iter(source_ids))
    parts = source_id.split(":")
    document_id = unquote(parts[1]) if len(parts) == 4 and parts[0] == "pdf" else entry.text.split("\n", 1)[0]
    return _PDFCandidate(record.citation, entry.text, source_id, document_id)


async def _pdf_candidates(memory, question: str, limit: int) -> tuple[_PDFCandidate, ...]:
    from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest

    weights, expansions = _pdf_query(question)
    queries = tuple(dict.fromkeys((question, " ".join(weights), *expansions)))
    found = {}
    pending = {}
    # Public Memory search provides native candidates. Listing supplies corpus
    # statistics and avoids truncating the corpus at a coarse FTS top-k cutoff.
    for query in queries:
        if not query.strip():
            continue
        page = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=max(80, limit * 4)))
        for hit in page.hits:
            citation = MemoryCitation(
                memory_ref=hit.memory_ref, entry_id=hit.entry_id, entry_version_id=hit.entry_version_id
            )
            pending[citation.model_dump_json()] = citation
    list_entries = getattr(memory, "list", None)
    if list_entries is not None:
        for record in (await list_entries()).entries:
            candidate = _pdf_candidate(record)
            if candidate is not None:
                found[record.citation.model_dump_json()] = candidate
    for key, citation in pending.items():
        if key not in found:
            found[key] = _pdf_candidate(await memory.get(GetMemoryEntryRequest(citation=citation)))
    return tuple(candidate for candidate in found.values() if candidate is not None)


def _pdf_rank(  # noqa: C901 - corpus scoring and complementary statement selection
    candidates: tuple[_PDFCandidate, ...], question: str
) -> tuple[_PDFCandidate, ...]:
    weights, _expansions = _pdf_query(question)
    targets = _pdf_document_targets(question, {c.document_id for c in candidates})
    if targets:
        candidates = tuple(c for c in candidates if c.document_id in targets)
    if not candidates:
        return ()
    # Document IDs and boilerplate question wording should not dominate the
    # page-level lexical score within a selected report.
    document_terms = {t for c in candidates for t in _pdf_tokens(c.document_id)}
    weights = {term: weight for term, weight in weights.items() if term not in document_terms}
    documents = [(c, _pdf_tokens(c.text.removeprefix(c.document_id).lstrip("\n"))) for c in candidates]
    frequency = Counter(term for _candidate, tokens in documents for term in set(tokens))
    average = max(1.0, sum(len(tokens) for _candidate, tokens in documents) / len(documents))
    idf = {term: math.log(1 + (len(documents) - frequency[term] + 0.5) / (frequency[term] + 0.5)) for term in weights}
    original = {term for term, weight in weights.items() if weight == 1.0}
    pairs = set(pairwise(t for t in _pdf_tokens(question) if t in original))
    intents = _pdf_statement_intents(question)
    geographic = bool(re.search(r"\bgeograph\w*\b|\bcountries\b|\bregions?\b", question.lower()))
    years = set(re.findall(r"(?:19|20)\d{2}", question))
    scored = []
    for candidate, tokens in documents:
        counts = Counter(tokens)
        normalization = 1.2 * (0.6 + 0.4 * len(tokens) / average)
        score = sum(
            weight * idf[term] * 2.2 * counts[term] / (counts[term] + normalization)
            for term, weight in weights.items()
            if counts[term]
        )
        score += 2 * len(original & counts.keys()) / max(1, len(original))
        # Short passages recover focused evidence on otherwise long pages.
        passage = max(
            (
                sum(weights.get(term, 0) * idf.get(term, 0) for term in set(tokens[start : start + 100]))
                for start in range(0, len(tokens), 50)
            ),
            default=0.0,
        )
        score += 0.35 * passage
        token_pairs = set(pairwise(tokens))
        score += 0.5 * len(pairs & token_pairs)
        heading = candidate.text.removeprefix(candidate.document_id).lstrip("\n")[:700]
        score += 0.4 * sum(weights.get(term, 0) for term in set(_pdf_tokens(heading)))
        kind = _pdf_statement_kind(heading)
        if kind in intents:
            score += 6.0
            score += 0.5 * len(years & set(re.findall(r"(?:19|20)\d{2}", heading)))
        if geographic and re.search(
            r"reportable segments|geographic(?:al)? (?:areas|regions)|countries and territories",
            candidate.text.lower(),
        ):
            score += 7.0
        scored.append((score, kind, candidate))
    scored.sort(key=lambda row: (-row[0], row[2].source_id))
    # Multi-statement questions need complementary pages, not twenty pages
    # that merely mention a statement. Reserve at most two true titles per
    # requested family and leave the remainder to lexical relevance.
    selected = []
    seen = set()
    for kind in intents:
        for _score, _actual_kind, candidate in [row for row in scored if row[1] == kind][:2]:
            if candidate.source_id not in seen:
                selected.append(candidate)
                seen.add(candidate.source_id)
    for score, _kind, candidate in scored:
        if score > 0 and candidate.source_id not in seen:
            selected.append(candidate)
            seen.add(candidate.source_id)
    return _pdf_include_continuations(tuple(selected), candidates)


def _pdf_include_continuations(
    ranked: tuple[_PDFCandidate, ...], candidates: tuple[_PDFCandidate, ...]
) -> tuple[_PDFCandidate, ...]:
    by_page = {candidate.source_id: candidate for candidate in candidates}
    continuations = {}
    for candidate in ranked[:3]:
        prefix, separator, number = candidate.source_id.rpartition(":")
        if not separator or not number.isdigit():
            continue
        following = by_page.get(f"{prefix}:{int(number) + 1}")
        if following is None:
            continue
        text = following.text.removeprefix(following.document_id).lstrip()
        text = re.sub(r"^Table of Contents\s*", "", text, flags=re.IGNORECASE)
        if re.match(r"(?:\d+[).]|\([a-z0-9]+\)|[a-z])", text):
            continuations[candidate.source_id] = following
    ordered = []
    seen = set()
    for candidate in ranked:
        for page in (candidate, continuations.get(candidate.source_id)):
            if page is not None and page.source_id not in seen:
                ordered.append(page)
                seen.add(page.source_id)
    return tuple(ordered)


async def search_pdf_pages(memory, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    """Search persisted scoped Memory and return distinct, exact PDF-page citations."""

    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must not be empty or blank")  # noqa: TRY003
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")  # noqa: TRY003
    candidates = await _pdf_candidates(memory, question, limit)
    return tuple(candidate.citation for candidate in _pdf_rank(candidates, question)[:limit])


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
    "search_pdf_pages",
]
