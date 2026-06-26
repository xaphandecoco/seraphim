"""Field registry for the advanced search engine (S09).

CN-25: imports ONLY from app.models, app.database, app.services, SQLAlchemy, stdlib.
Never imports from any router or app.dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any, Callable, Literal, Optional

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import engine as _app_engine
from app.models import Contact
from app.services import custom_fields as _cf_svc


# ---------------------------------------------------------------------------
# Operator sets per value_type
# ---------------------------------------------------------------------------

_TEXT_OPS: frozenset[str] = frozenset({
    "eq", "ne", "contains", "not_contains", "starts_with", "ends_with",
    "in", "is_set", "is_empty",
})
_ENUM_OPS: frozenset[str] = frozenset({
    "eq", "ne", "in", "is_set", "is_empty",
})
_MULTISELECT_OPS: frozenset[str] = frozenset({
    "contains", "contains_any", "is_set", "is_empty",
})
_NUMBER_OPS: frozenset[str] = frozenset({
    "eq", "ne", "gt", "gte", "lt", "lte", "between", "in", "is_set", "is_empty",
})
_INT_OPS: frozenset[str] = _NUMBER_OPS
_DATE_OPS: frozenset[str] = frozenset({
    "eq", "ne", "before", "after", "on_or_before", "on_or_after",
    "between", "is_set", "is_empty",
})
_DATETIME_OPS: frozenset[str] = _DATE_OPS
_BOOL_OPS: frozenset[str] = frozenset({
    "eq", "is_set", "is_empty",
})
_CONTACT_REF_OPS: frozenset[str] = frozenset({
    "eq", "in", "is_set", "is_empty",
})


def _ops_for_type(value_type: str) -> frozenset[str]:
    """Return the allowed operator set for a given value_type."""
    mapping: dict[str, frozenset[str]] = {
        "string": _TEXT_OPS,
        "int": _INT_OPS,
        "number": _NUMBER_OPS,
        "bool": _BOOL_OPS,
        "date": _DATE_OPS,
        "datetime": _DATETIME_OPS,
        "enum": _ENUM_OPS,
        "multiselect": _MULTISELECT_OPS,
        "contact_reference": _CONTACT_REF_OPS,
    }
    return mapping.get(value_type, frozenset())


# ---------------------------------------------------------------------------
# FieldSpec dataclass
# ---------------------------------------------------------------------------


@dataclass
class FieldSpec:
    """Descriptor for one searchable field.

    resolve(dialect) returns a SQLAlchemy column expression that can be used
    in WHERE clauses.  The expression is parameterized — no user input is ever
    interpolated into it.
    """

    key: str
    label: str
    kind: Literal["core", "derived", "custom"]
    value_type: Literal[
        "string", "int", "number", "bool", "date", "datetime",
        "enum", "multiselect", "contact_reference"
    ]
    allowed_ops: frozenset[str]
    options: Optional[list[dict[str, str]]]
    nullable: bool
    _resolver: Callable[[str], Any] = dc_field(default=None, repr=False)  # type: ignore[assignment]

    def resolve(self, dialect: str) -> Any:
        """Return a SQLAlchemy column expression for WHERE clause building."""
        if self._resolver is not None:
            return self._resolver(dialect)
        raise ValueError(f"No resolver configured for field '{self.key}'")


# ---------------------------------------------------------------------------
# Resolver factories
# ---------------------------------------------------------------------------


def _col_resolver(col: Any) -> Callable[[str], Any]:
    """Return a resolver that always returns the given ORM column (dialect-agnostic)."""
    def resolver(dialect: str) -> Any:  # noqa: ARG001
        return col
    return resolver


def _custom_resolver(name: str) -> Callable[[str], Any]:
    """Return a dialect-branched resolver for a custom_data JSON key."""
    def resolver(dialect: str) -> Any:
        if dialect == "postgresql":
            # JSONB text extraction — mirrors custom_fields.py:446
            return Contact.custom_data[name].astext  # type: ignore[index]
        else:
            # SQLite — mirrors custom_fields.py:474
            return func.json_extract(Contact.custom_data, f"$.{name}")
    return resolver


# ---------------------------------------------------------------------------
# Custom data_type → value_type mapping
# ---------------------------------------------------------------------------

_CF_TYPE_MAP: dict[str, str] = {
    "text": "string",
    "textarea": "string",
    "select": "enum",
    "multiselect": "multiselect",
    "date": "date",
    "number": "number",
    "checkbox": "bool",
    "contact_reference": "contact_reference",
}


# ---------------------------------------------------------------------------
# Static registry entries (core + derived)
# ---------------------------------------------------------------------------

# Tier options (lowercase per spec)
_TIER_OPTIONS: list[dict[str, str]] = [
    {"value": "tier0", "label": "Tier 0"},
    {"value": "tier1", "label": "Tier 1"},
    {"value": "tier2", "label": "Tier 2"},
    {"value": "tier3", "label": "Tier 3"},
    {"value": "inactive", "label": "Inactive"},
]

# Contact type options
_CONTACT_TYPE_OPTIONS: list[dict[str, str]] = [
    {"value": "individual", "label": "Individual"},
    {"value": "household", "label": "Household"},
    {"value": "organization", "label": "Organization"},
]


def _build_static_specs() -> dict[str, FieldSpec]:
    """Build the static (core + derived) field specs."""
    specs: dict[str, FieldSpec] = {}

    # CORE fields — resolve to Contact.<col>
    core_defs: list[tuple[str, str, str, Any, bool, Optional[list[dict[str, str]]]]] = [
        ("first_name",     "First Name",     "string",   Contact.first_name,     False, None),
        ("last_name",      "Last Name",      "string",   Contact.last_name,      False, None),
        ("nickname",       "Nickname",       "string",   Contact.nickname,       True,  None),
        ("suffix",         "Suffix",         "string",   Contact.suffix,         True,  None),
        ("email",          "Email",          "string",   Contact.email,          True,  None),
        ("phone",          "Phone",          "string",   Contact.phone,          True,  None),
        ("street_address", "Street Address", "string",   Contact.street_address, True,  None),
        ("gender",         "Gender",         "string",   Contact.gender,         True,  None),
        (
            "contact_type", "Contact Type", "enum",
            Contact.contact_type, False, _CONTACT_TYPE_OPTIONS,
        ),
        ("contact_subtype", "Contact Subtype", "string", Contact.contact_subtype, True, None),
        ("birth_date",     "Birth Date",     "date",     Contact.birth_date,     True,  None),
        ("external_id",    "External ID",    "int",      Contact.external_id,    True,  None),
        ("created_at",     "Created At",     "datetime", Contact.created_at,     False, None),
        ("updated_at",     "Updated At",     "datetime", Contact.updated_at,     False, None),
    ]

    for key, label, value_type, col, nullable, options in core_defs:
        specs[key] = FieldSpec(
            key=key,
            label=label,
            kind="core",
            value_type=value_type,  # type: ignore[arg-type]
            allowed_ops=_ops_for_type(value_type),
            options=options,
            nullable=nullable,
            _resolver=_col_resolver(col),
        )

    # DERIVED fields — plain contacts snapshot columns
    derived_defs: list[tuple[str, str, str, Any, bool, Optional[list[dict[str, str]]]]] = [
        ("tier",             "Tier",             "enum",     Contact.tier,             True, _TIER_OPTIONS),
        ("is_active",        "Is Active",        "bool",     Contact.is_active,        True, None),
        ("is_regular",       "Is Regular",       "bool",     Contact.is_regular,       True, None),
        ("is_connected",     "Is Connected",     "bool",     Contact.is_connected,     True, None),
        ("weeks_absent",     "Weeks Absent",     "int",      Contact.weeks_absent,     True, None),
        ("attendance_count", "Attendance Count", "int",      Contact.attendance_count, True, None),
        ("last_attended_at", "Last Attended At", "datetime", Contact.last_attended_at, True, None),
    ]

    for key, label, value_type, col, nullable, options in derived_defs:
        specs[key] = FieldSpec(
            key=key,
            label=label,
            kind="derived",
            value_type=value_type,  # type: ignore[arg-type]
            allowed_ops=_ops_for_type(value_type),
            options=options,
            nullable=nullable,
            _resolver=_col_resolver(col),
        )

    return specs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def build_registry(db: AsyncSession) -> dict[str, FieldSpec]:
    """Build the complete field registry for contact searches.

    Combines static core+derived fields with active custom fields from S02.
    Custom fields are keyed as ``custom.<name>``.

    Not cached at module level — custom-field schema may change between requests.
    """
    specs = _build_static_specs()

    # Append custom fields from S02 schema
    schema_entries = await _cf_svc.get_active_schema(db, "contact")
    for entry in schema_entries:
        for cf_def in entry["defs"]:
            name: str = cf_def.name
            data_type: str = cf_def.data_type
            value_type = _CF_TYPE_MAP.get(data_type, "string")
            key = f"custom.{name}"

            # Build options list for enum/multiselect custom fields
            options: Optional[list[dict[str, str]]] = None
            if data_type in ("select", "multiselect") and cf_def.options:
                options = [
                    {"value": str(o["value"]), "label": str(o["label"])}
                    for o in cf_def.options
                ]

            specs[key] = FieldSpec(
                key=key,
                label=cf_def.label,
                kind="custom",
                value_type=value_type,  # type: ignore[arg-type]
                allowed_ops=_ops_for_type(value_type),
                options=options,
                nullable=True,
                _resolver=_custom_resolver(name),
            )

    return specs
