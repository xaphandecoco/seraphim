"""QA tests for T03 — Pydantic schemas: ImportBatchOut, ImportRowResultOut,
list/detail/summary responses.

Validates all acceptance criteria:
1. ImportBatchOut(ConfigDict(from_attributes=True)) with all fields per spec §4.1;
   source_filename/finished_at/created_by_id are Optional.
2. ImportBatchListResponse(items: list[ImportBatchOut], total: int, limit: int, offset: int).
3. ImportBatchDetailResponse(ImportBatchOut) adds pending_review_count: int.
4. ImportRowResultOut(from_attributes=True) with 8 fields per §4.1; raw field is omitted.
5. ImportRowResultListResponse(items: list[ImportRowResultOut], total: int, limit: int, offset: int).
6. MigrationSummaryResponse with contacts/events/participants/links each Optional[ImportBatchOut] = None
   and pending_reviews: int.
7. Pydantic v2 ConfigDict(from_attributes=True) consistent with existing schemas.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import get_args, get_origin

from app.schemas import (
    ImportBatchDetailResponse,
    ImportBatchListResponse,
    ImportBatchOut,
    ImportRowResultListResponse,
    ImportRowResultOut,
    MigrationSummaryResponse,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BATCH_REQUIRED_FIELDS = {
    "id", "entity", "mode", "status", "column_map", "options",
    "total_rows", "created_count", "updated_count", "skipped_count",
    "error_count", "review_count", "started_at",
}
BATCH_OPTIONAL_FIELDS = {"source_filename", "finished_at", "created_by_id"}
BATCH_ALL_FIELDS = BATCH_REQUIRED_FIELDS | BATCH_OPTIONAL_FIELDS

ROW_REQUIRED_FIELDS = {"id", "batch_id", "row_number", "outcome", "created_at"}
ROW_OPTIONAL_FIELDS = {"external_id", "entity_id", "message"}
ROW_ALL_FIELDS = ROW_REQUIRED_FIELDS | ROW_OPTIONAL_FIELDS

_NOW = datetime.now(timezone.utc).replace(tzinfo=None)

VALID_BATCH_DATA = {
    "id": 1,
    "entity": "contact",
    "mode": "create",
    "status": "completed",
    "column_map": {"first_name": "First Name"},
    "options": {"skip_errors": False},
    "total_rows": 100,
    "created_count": 80,
    "updated_count": 10,
    "skipped_count": 5,
    "error_count": 5,
    "review_count": 2,
    "started_at": _NOW,
}

VALID_ROW_DATA = {
    "id": 1,
    "batch_id": 1,
    "row_number": 1,
    "outcome": "created",
    "created_at": _NOW,
}


# ---------------------------------------------------------------------------
# AC1: ImportBatchOut fields and ConfigDict
# ---------------------------------------------------------------------------

class TestImportBatchOut:
    def test_from_attributes_true(self):
        config = ImportBatchOut.model_config
        assert config.get("from_attributes") is True, (
            "ImportBatchOut must have ConfigDict(from_attributes=True)"
        )

    def test_all_expected_fields_present(self):
        actual = set(ImportBatchOut.model_fields.keys())
        assert BATCH_ALL_FIELDS.issubset(actual), (
            f"Missing fields: {BATCH_ALL_FIELDS - actual}"
        )

    def test_optional_fields_have_none_default(self):
        """source_filename, finished_at, created_by_id must be Optional with None default."""
        for field_name in BATCH_OPTIONAL_FIELDS:
            field = ImportBatchOut.model_fields[field_name]
            assert not field.is_required(), (
                f"{field_name} should be Optional (not required)"
            )
            assert field.default is None, (
                f"{field_name} default should be None, got {field.default}"
            )

    def test_required_fields_are_required(self):
        for field_name in BATCH_REQUIRED_FIELDS:
            field = ImportBatchOut.model_fields[field_name]
            assert field.is_required(), (
                f"{field_name} should be required but has a default"
            )

    def test_valid_instantiation(self):
        obj = ImportBatchOut(**VALID_BATCH_DATA)
        assert obj.id == 1
        assert obj.source_filename is None
        assert obj.finished_at is None
        assert obj.created_by_id is None

    def test_optional_fields_accept_values(self):
        obj = ImportBatchOut(
            **VALID_BATCH_DATA,
            source_filename="data.xlsx",
            finished_at=_NOW,
            created_by_id=42,
        )
        assert obj.source_filename == "data.xlsx"
        assert obj.finished_at == _NOW
        assert obj.created_by_id == 42

    def test_from_orm_attributes(self):
        """Verify from_attributes=True allows ORM-style instantiation via model_validate."""
        class FakeOrm:
            id = 1
            source_filename = None
            entity = "contact"
            mode = "create"
            status = "completed"
            column_map = {}
            options = {}
            total_rows = 10
            created_count = 8
            updated_count = 1
            skipped_count = 1
            error_count = 0
            review_count = 0
            started_at = _NOW
            finished_at = None
            created_by_id = None

        obj = ImportBatchOut.model_validate(FakeOrm())
        assert obj.entity == "contact"
        assert obj.id == 1


# ---------------------------------------------------------------------------
# AC2: ImportBatchListResponse
# ---------------------------------------------------------------------------

class TestImportBatchListResponse:
    def test_fields_present(self):
        actual = set(ImportBatchListResponse.model_fields.keys())
        assert {"items", "total", "limit", "offset"} == actual

    def test_items_type_is_list_of_batch_out(self):
        field = ImportBatchListResponse.model_fields["items"]
        annotation = field.annotation
        origin = get_origin(annotation)
        assert origin is list, f"items should be list, got {origin}"
        args = get_args(annotation)
        assert args[0] is ImportBatchOut, f"items element type should be ImportBatchOut, got {args[0]}"

    def test_valid_instantiation(self):
        batch = ImportBatchOut(**VALID_BATCH_DATA)
        resp = ImportBatchListResponse(items=[batch], total=1, limit=50, offset=0)
        assert resp.total == 1
        assert len(resp.items) == 1

    def test_all_numeric_fields_required(self):
        for field_name in ("total", "limit", "offset"):
            assert ImportBatchListResponse.model_fields[field_name].is_required()

    def test_empty_items(self):
        resp = ImportBatchListResponse(items=[], total=0, limit=50, offset=0)
        assert resp.items == []
        assert resp.total == 0


# ---------------------------------------------------------------------------
# AC3: ImportBatchDetailResponse
# ---------------------------------------------------------------------------

class TestImportBatchDetailResponse:
    def test_inherits_from_batch_out(self):
        assert issubclass(ImportBatchDetailResponse, ImportBatchOut), (
            "ImportBatchDetailResponse must extend ImportBatchOut"
        )

    def test_adds_pending_review_count(self):
        assert "pending_review_count" in ImportBatchDetailResponse.model_fields

    def test_pending_review_count_is_int(self):
        field = ImportBatchDetailResponse.model_fields["pending_review_count"]
        assert field.annotation is int

    def test_pending_review_count_is_required(self):
        field = ImportBatchDetailResponse.model_fields["pending_review_count"]
        assert field.is_required()

    def test_valid_instantiation(self):
        obj = ImportBatchDetailResponse(**VALID_BATCH_DATA, pending_review_count=3)
        assert obj.pending_review_count == 3
        assert obj.id == 1

    def test_all_parent_fields_inherited(self):
        actual = set(ImportBatchDetailResponse.model_fields.keys())
        assert BATCH_ALL_FIELDS.issubset(actual)

    def test_from_attributes_inherited(self):
        config = ImportBatchDetailResponse.model_config
        assert config.get("from_attributes") is True


# ---------------------------------------------------------------------------
# AC4: ImportRowResultOut — 8 fields, no raw, from_attributes=True
# ---------------------------------------------------------------------------

class TestImportRowResultOut:
    def test_from_attributes_true(self):
        config = ImportRowResultOut.model_config
        assert config.get("from_attributes") is True

    def test_exactly_8_fields(self):
        actual = set(ImportRowResultOut.model_fields.keys())
        assert actual == ROW_ALL_FIELDS, (
            f"Expected {ROW_ALL_FIELDS}, got {actual}"
        )

    def test_raw_field_absent(self):
        assert "raw" not in ImportRowResultOut.model_fields, (
            "raw field must be omitted from ImportRowResultOut (not in list schema)"
        )

    def test_optional_fields_have_none_default(self):
        for field_name in ROW_OPTIONAL_FIELDS:
            field = ImportRowResultOut.model_fields[field_name]
            assert not field.is_required(), f"{field_name} should be Optional"
            assert field.default is None

    def test_required_fields_are_required(self):
        for field_name in ROW_REQUIRED_FIELDS:
            field = ImportRowResultOut.model_fields[field_name]
            assert field.is_required(), f"{field_name} should be required"

    def test_valid_instantiation_minimal(self):
        obj = ImportRowResultOut(**VALID_ROW_DATA)
        assert obj.external_id is None
        assert obj.entity_id is None
        assert obj.message is None

    def test_valid_instantiation_full(self):
        obj = ImportRowResultOut(
            **VALID_ROW_DATA,
            external_id="CIV-123",
            entity_id=99,
            message="Matched by external_id",
        )
        assert obj.external_id == "CIV-123"
        assert obj.entity_id == 99
        assert obj.message == "Matched by external_id"

    def test_from_orm_attributes(self):
        class FakeRow:
            id = 5
            batch_id = 1
            row_number = 42
            external_id = "EXT-1"
            outcome = "skipped"
            entity_id = None
            message = "Duplicate"
            created_at = _NOW

        obj = ImportRowResultOut.model_validate(FakeRow())
        assert obj.row_number == 42
        assert obj.outcome == "skipped"


# ---------------------------------------------------------------------------
# AC5: ImportRowResultListResponse
# ---------------------------------------------------------------------------

class TestImportRowResultListResponse:
    def test_fields_present(self):
        actual = set(ImportRowResultListResponse.model_fields.keys())
        assert {"items", "total", "limit", "offset"} == actual

    def test_items_type_is_list_of_row_out(self):
        field = ImportRowResultListResponse.model_fields["items"]
        annotation = field.annotation
        origin = get_origin(annotation)
        assert origin is list
        args = get_args(annotation)
        assert args[0] is ImportRowResultOut

    def test_valid_instantiation(self):
        row = ImportRowResultOut(**VALID_ROW_DATA)
        resp = ImportRowResultListResponse(items=[row], total=1, limit=100, offset=0)
        assert resp.total == 1

    def test_empty_items(self):
        resp = ImportRowResultListResponse(items=[], total=0, limit=200, offset=0)
        assert resp.items == []


# ---------------------------------------------------------------------------
# AC6: MigrationSummaryResponse
# ---------------------------------------------------------------------------

class TestMigrationSummaryResponse:
    def test_entity_fields_optional_with_none_default(self):
        for field_name in ("contacts", "events", "participants", "links"):
            field = MigrationSummaryResponse.model_fields[field_name]
            assert not field.is_required(), f"{field_name} should be Optional"
            assert field.default is None, f"{field_name} default should be None"

    def test_entity_fields_type_is_optional_batch_out(self):
        for field_name in ("contacts", "events", "participants", "links"):
            field = MigrationSummaryResponse.model_fields[field_name]
            annotation = field.annotation
            # Should be ImportBatchOut | None (union)
            args = get_args(annotation)
            assert ImportBatchOut in args, (
                f"{field_name} annotation {annotation} should include ImportBatchOut"
            )

    def test_pending_reviews_is_required_int(self):
        field = MigrationSummaryResponse.model_fields["pending_reviews"]
        assert field.is_required()
        assert field.annotation is int

    def test_valid_instantiation_all_none(self):
        resp = MigrationSummaryResponse(pending_reviews=0)
        assert resp.contacts is None
        assert resp.events is None
        assert resp.participants is None
        assert resp.links is None
        assert resp.pending_reviews == 0

    def test_valid_instantiation_with_batches(self):
        batch = ImportBatchOut(**VALID_BATCH_DATA)
        resp = MigrationSummaryResponse(
            contacts=batch,
            events=batch,
            participants=None,
            links=None,
            pending_reviews=5,
        )
        assert resp.contacts is not None
        assert resp.contacts.entity == "contact"
        assert resp.pending_reviews == 5

    def test_all_five_fields_present(self):
        actual = set(MigrationSummaryResponse.model_fields.keys())
        assert {"contacts", "events", "participants", "links", "pending_reviews"} == actual


# ---------------------------------------------------------------------------
# AC7: Pydantic v2 ConfigDict consistency
# ---------------------------------------------------------------------------

class TestPydanticV2Consistency:
    def test_no_class_based_config_on_import_schemas(self):
        """Verify none of the import schemas use old-style class Meta/Config."""
        for schema_cls in (
            ImportBatchOut,
            ImportBatchListResponse,
            ImportBatchDetailResponse,
            ImportRowResultOut,
            ImportRowResultListResponse,
            MigrationSummaryResponse,
        ):
            assert not hasattr(schema_cls, "Config") or not isinstance(
                getattr(schema_cls, "Config", None), type
            ), f"{schema_cls.__name__} uses old-style Config class"

    def test_orm_schemas_have_from_attributes(self):
        """Both ORM-reading schemas must have from_attributes=True."""
        for schema_cls in (ImportBatchOut, ImportRowResultOut):
            assert schema_cls.model_config.get("from_attributes") is True, (
                f"{schema_cls.__name__} must have ConfigDict(from_attributes=True)"
            )
