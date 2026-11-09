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
import hashlib
import io

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, IndirectObject, NameObject

from powercontext.sources import SourceDefinitionRegistry, SourceMaterialization, project_source_for_transport
from powercontext.sources.pdf_pages import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
    PDFPageInput,
    parse_pdf_document,
    pdf_page_source_id,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY


def _text_page(writer: PdfWriter, text: bytes) -> None:
    page = writer.add_blank_page(width=600, height=800)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
    })
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 700 Td (" + text + b") Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)


def _bytes(writer: PdfWriter) -> bytes:
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_full_pdf_preserves_text_numbers_blank_pages_and_source_projection() -> None:
    writer = PdfWriter()
    _text_page(writer, b"Revenue 12345.67 Expenses 4321")
    writer.add_blank_page(width=600, height=800)
    _text_page(writer, b"Net income 8024.67")
    raw = _bytes(writer)
    pages = parse_pdf_document(PDFDocumentInput(document_id="Example / report", content=raw))
    assert [page.page_number for page in pages] == [1, 2, 3]
    assert [page.text.strip() for page in pages] == [
        "Revenue 12345.67 Expenses 4321",
        "",
        "Net income 8024.67",
    ]
    assert all(page.document_sha256 == hashlib.sha256(raw).hexdigest() for page in pages)

    async def check() -> None:
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        for page in pages:
            source = await registry.resolve(page)
            assert source.materialization == SourceMaterialization.CAPTURED
            assert await registry.read(source) == page
            text = project_source_for_transport(registry, source).projection(TEXT_EVIDENCE_PROJECTION_KEY)
            assert text["content"] == page.document_id + "\n" + page.text
            assert text["source_type"] == "pdf-page"
            assert text["source_id"] == source.name
            assert text["metadata"] == {
                "document_id": page.document_id,
                "document_sha256": page.document_sha256,
                "page_number": page.page_number,
            }

    asyncio.run(check())


def test_unreadable_later_page_fails_instead_of_becoming_a_blank_page() -> None:
    writer = PdfWriter()
    _text_page(writer, b"Valid first page")
    page = writer.add_blank_page(width=600, height=800)
    page[NameObject("/Contents")] = IndirectObject(9999, 0, writer)
    with pytest.raises(ValueError):
        parse_pdf_document(PDFDocumentInput(document_id="broken-last-page", content=_bytes(writer)))


def test_long_ids_do_not_collide_with_literal_digest_ids() -> None:
    long_id = "financial-report-" * 100
    digest_id = hashlib.sha256(long_id.encode()).hexdigest()
    identifiers = [long_id, digest_id, "sha256:" + digest_id, "a:b", "a%3Ab", "a/b", "a%2Fb", "财报" * 100]
    ids = [pdf_page_source_id(value, "f" * 64, 1) for value in identifiers]
    assert len(set(ids)) == len(identifiers)
    assert all(len(value) <= 256 for value in ids)
    assert ids == [pdf_page_source_id(value, "f" * 64, 1) for value in identifiers]


@pytest.mark.parametrize("document_id", ["", " ", "\t\n"])
def test_blank_document_ids_fail(document_id: str) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    with pytest.raises(ValueError):
        parse_pdf_document(PDFDocumentInput(document_id=document_id, content=_bytes(writer)))


@pytest.mark.parametrize("page_number", [0, -1, True, 1.5])
def test_invalid_physical_page_numbers_fail(page_number: object) -> None:
    with pytest.raises(ValueError):
        PDFPageInput(document_id="report", document_sha256="a" * 64, page_number=page_number, text="")


def test_encryption_with_nonempty_user_password_fails() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt(user_password="secret", owner_password="owner")  # noqa: S106 - generated test fixture
    with pytest.raises(ValueError):
        parse_pdf_document(PDFDocumentInput(document_id="password", content=_bytes(writer)))
