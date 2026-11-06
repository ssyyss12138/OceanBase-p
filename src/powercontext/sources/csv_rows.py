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

"""Captured CSV rows with stable primary-key identities."""

from __future__ import annotations

import csv
import io
import json
from typing import Literal
from urllib.parse import quote

from pydantic import BaseModel, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

CSV_ROW_SOURCE_NAME = "csv-row"


class CSVDocumentInput(BaseModel):
    """One complete offline CSV document and its row identity fields."""

    document_id: str
    primary_key: str
    content: bytes


class CSVRowInput(BaseModel):
    """One validated CSV record with its original header and field values."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @model_validator(mode="after")
    def validate_row(self) -> CSVRowInput:
        _validate_document_id(self.document_id)
        _validate_columns(self.columns)
        if not self.primary_key:
            raise ValueError("primary_key must not be empty")  # noqa: TRY003
        try:
            primary_key_index = self.columns.index(self.primary_key)
        except ValueError as error:
            raise ValueError("primary_key must exactly match a column") from error  # noqa: TRY003
        if len(self.values) != len(self.columns):
            raise ValueError("row width must match the header width")  # noqa: TRY003
        _validate_primary_key_value(self.primary_key_value)
        if self.values[primary_key_index] != self.primary_key_value:
            raise ValueError("primary_key_value must match the row value")  # noqa: TRY003
        csv_row_source_id(self.document_id, self.primary_key_value)
        return self


class CSVRowSource(Source):
    """A captured CSV record addressable by document and primary key."""

    materialization: Literal[SourceMaterialization.CAPTURED] = SourceMaterialization.CAPTURED
    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @model_validator(mode="after")
    def validate_identity_and_row(self) -> CSVRowSource:
        row = CSVRowInput(
            document_id=self.document_id,
            primary_key=self.primary_key,
            primary_key_value=self.primary_key_value,
            columns=self.columns,
            values=self.values,
        )
        expected_id = csv_row_source_id(row.document_id, row.primary_key_value)
        if self.name != expected_id or self.source_id != expected_id:
            raise ValueError("name and source_id must match the CSV row identity")  # noqa: TRY003
        return self


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:  # noqa: C901
    """Parse and validate a complete CSV document atomically."""

    _validate_document_id(value.document_id)
    if not value.primary_key:
        raise ValueError("primary_key must not be empty")  # noqa: TRY003

    try:
        text = value.content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("CSV content must be valid UTF-8") from error  # noqa: TRY003

    reader = csv.reader(
        io.StringIO(text, newline=""),
        delimiter=",",
        quotechar='"',
        doublequote=True,
        strict=True,
    )
    try:
        header = next(reader)
    except StopIteration as error:
        raise ValueError("CSV document must contain a header") from error  # noqa: TRY003
    except csv.Error as error:
        raise ValueError("invalid CSV header") from error  # noqa: TRY003

    columns = tuple(header)
    _validate_columns(columns)
    try:
        primary_key_index = columns.index(value.primary_key)
    except ValueError as error:
        raise ValueError("primary_key must exactly match a column") from error  # noqa: TRY003

    rows: list[CSVRowInput] = []
    primary_key_values: set[str] = set()
    try:
        for record in reader:
            if not record:
                continue
            if len(record) != len(columns):
                raise ValueError("row width must match the header width")  # noqa: TRY003
            values = tuple(record)
            primary_key_value = values[primary_key_index]
            _validate_primary_key_value(primary_key_value)
            if primary_key_value in primary_key_values:
                raise ValueError("primary key values must be unique within a document")  # noqa: TRY003
            primary_key_values.add(primary_key_value)
            rows.append(
                CSVRowInput(
                    document_id=value.document_id,
                    primary_key=value.primary_key,
                    primary_key_value=primary_key_value,
                    columns=columns,
                    values=values,
                )
            )
    except csv.Error as error:
        raise ValueError("invalid CSV content") from error  # noqa: TRY003

    return tuple(rows)


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Return the bounded, delimiter-safe identity for one CSV row."""

    _validate_document_id(document_id)
    _validate_primary_key_value(primary_key_value)
    source_id = f"csv:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"CSV row source_id must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return source_id


class CSVRowSourceAdapter:
    """Resolve and read captured CSV row Sources."""

    input_class = CSVRowInput
    name = CSV_ROW_SOURCE_NAME
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        source_id = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=source_id,
            source_id=source_id,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


class CSVRowTextEvidenceProjection:
    """Expose a CSV record as canonical ordered JSON text evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


def _validate_document_id(document_id: str) -> None:
    if not document_id or not document_id.strip():
        raise ValueError("document_id must not be empty or blank")  # noqa: TRY003
    if document_id != document_id.strip():
        raise ValueError("document_id must not contain leading or trailing whitespace")  # noqa: TRY003


def _validate_primary_key_value(primary_key_value: str) -> None:
    if not primary_key_value or not primary_key_value.strip():
        raise ValueError("primary key value must not be empty or blank")  # noqa: TRY003


def _validate_columns(columns: tuple[str, ...]) -> None:
    if not columns:
        raise ValueError("CSV document must contain a non-empty header")  # noqa: TRY003
    if any(not column or not column.strip() for column in columns):
        raise ValueError("header fields must not be empty or blank")  # noqa: TRY003
    if len(set(columns)) != len(columns):
        raise ValueError("header fields must be unique")  # noqa: TRY003


CSV_ROW_SOURCE_ADAPTER = CSVRowSourceAdapter()
CSV_ROW_SOURCE_DEFINITION = AdapterSourceDefinition(
    CSV_ROW_SOURCE_ADAPTER,
    projections=(CSVRowTextEvidenceProjection(),),
)


__all__ = [
    "CSV_ROW_SOURCE_DEFINITION",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "csv_row_source_id",
    "parse_csv_document",
]
