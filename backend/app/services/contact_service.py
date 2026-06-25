"""Contact CRUD service.

Ownership: F01.
Module-level async functions following the same pattern as audit.py and
custom_fields.py.  No class wrapper.

CN-25: imports only from app.models, app.database, app.services.audit,
app.services.custom_fields, SQLAlchemy, FastAPI, and stdlib.
Never imports from any router or app.dependencies.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any, Optional

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ComprefaceSubject,
    Contact,
    Event,
    Participant,
    utc_now,
)
from app.services import audit as audit_svc
from app.services.custom_fields import validate_and_coerce

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_AUDIT_FIELDS = [
    "first_name",
    "last_name",
    "nickname",
    "suffix",
    "gender",
    "birth_date",
    "phone",
    "email",
    "street_address",
    "contact_type",
    "contact_subtype",
    "custom_data",
]

_ASSIGNABLE_CORE_FIELDS = {
    "contact_type",
    "contact_subtype",
    "first_name",
    "last_name",
    "nickname",
    "suffix",
    "gender",
    "birth_date",
    "phone",
    "email",
    "street_address",
}

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _display_name(contact: Contact) -> str:
    """Return the best human-readable name for *contact*."""
    if contact.nickname:
        return contact.nickname
    parts = [contact.first_name or "", contact.last_name or ""]
    name = " ".join(p for p in parts if p).strip()
    if contact.suffix:
        name = f"{name} {contact.suffix}".strip()
    return name or f"Contact #{contact.id}"


def _contact_to_audit_dict(contact: Contact) -> dict[str, Any]:
    """Build a serializable before/after dict from audit fields only."""
    result: dict[str, Any] = {}
    for field in _AUDIT_FIELDS:
        value = getattr(contact, field, None)
        if isinstance(value, date) and not isinstance(value, __import__("datetime").datetime):
            value = value.isoformat()
        result[field] = value
    return result


def _find_first_thumbnail(subject_id: str) -> Optional[str]:
    """Return /storage/-prefixed URL of the first enrolled thumbnail.

    Best-effort: never raises if directory is missing.
    """
    try:
        from app.config import legacy_settings as _ls
        base_path = os.environ.get("STORAGE_PATH", _ls.STORAGE_PATH)
        enrolled_dir = Path(base_path) / "enrolled" / subject_id
        if not enrolled_dir.exists():
            return None
        thumbs = sorted(enrolled_dir.glob("sample_*_thumb.jpg"))
        if not thumbs:
            return None
        return f"/storage/enrolled/{subject_id}/{thumbs[0].name}"
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def list_contacts(
    db: AsyncSession,
    *,
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 25,
    contact_type: Optional[str] = None,
    contact_subtype: Optional[str] = None,
    tier: Optional[str] = None,
    is_regular: Optional[bool] = None,
    include_deleted: bool = False,
) -> dict[str, Any]:
    """List contacts with pagination and optional filters.

    Executes exactly 2 queries: a COUNT and a paged SELECT.
    Returns a dict with keys: total, page, page_size, items (list of Contact ORM rows).
    """
    # Clamp pagination params
    page = max(page, 1)
    page_size = max(1, min(page_size, 100))

    # Build base filter conditions
    conditions = []
    if not include_deleted:
        conditions.append(Contact.is_deleted.is_(False))

    if search and search.strip():
        q = f"%{search.strip()}%"
        conditions.append(
            or_(
                Contact.first_name.ilike(q),
                Contact.last_name.ilike(q),
                Contact.nickname.ilike(q),
                Contact.email.ilike(q),
            )
        )

    if contact_type is not None:
        conditions.append(Contact.contact_type == contact_type)
    if contact_subtype is not None:
        conditions.append(Contact.contact_subtype == contact_subtype)
    if tier is not None:
        conditions.append(Contact.tier == tier)
    if is_regular is not None:
        conditions.append(Contact.is_regular == is_regular)

    # Query 1: count
    count_stmt = select(func.count()).select_from(Contact)
    if conditions:
        count_stmt = count_stmt.where(*conditions)
    count_result = await db.execute(count_stmt)
    total: int = count_result.scalar_one()

    # Query 2: paged select
    offset = (page - 1) * page_size
    items_stmt = (
        select(Contact)
        .order_by(Contact.last_name, Contact.first_name)
        .offset(offset)
        .limit(page_size)
    )
    if conditions:
        items_stmt = items_stmt.where(*conditions)
    items_result = await db.execute(items_stmt)
    items = list(items_result.scalars().all())

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
    }


async def get_contact_detail(
    db: AsyncSession,
    contact_id: int,
    *,
    allow_deleted: bool = False,
) -> dict[str, Any]:
    """Load a contact with all enrichment data.

    Raises HTTPException(404) if not found or deleted (unless allow_deleted).
    Returns a dict with: contact, display_name, custom_field_chips, face_summary, derived_badges.
    """
    result = await db.execute(
        select(Contact).where(Contact.id == contact_id)
    )
    contact = result.scalar_one_or_none()

    if contact is None or (contact.is_deleted and not allow_deleted):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contact {contact_id} not found",
        )

    # ---- Resolve contact_reference custom fields (no N+1) -------------------
    # Collect all integer IDs stored in custom_data that could be contact refs
    all_ref_ids: list[int] = []
    for value in (contact.custom_data or {}).values():
        if isinstance(value, int):
            all_ref_ids.append(value)
        elif isinstance(value, list):
            all_ref_ids.extend(v for v in value if isinstance(v, int))

    ref_chips: dict[int, dict[str, Any]] = {}
    if all_ref_ids:
        # Single batched query — no N+1
        ref_result = await db.execute(
            select(Contact.id, Contact.first_name, Contact.last_name,
                   Contact.nickname, Contact.contact_type)
            .where(Contact.id.in_(all_ref_ids))
        )
        for row in ref_result.all():
            cid, fn, ln, nick, ctype = row
            name = nick or f"{fn or ''} {ln or ''}".strip() or f"Contact #{cid}"
            ref_chips[cid] = {
                "id": cid,
                "display_name": name,
                "contact_type": ctype,
            }

    # ---- FaceSummary from ComprefaceSubject ----------------------------------
    subj_result = await db.execute(
        select(ComprefaceSubject).where(
            ComprefaceSubject.contact_id == contact_id
        )
    )
    subject = subj_result.scalar_one_or_none()

    enrolled = (
        subject is not None
        and getattr(subject, "enrollment_status", None) == "active"
    )
    sample_count = subject.sample_count if subject else 0
    face_thumbnail_path: Optional[str] = None
    if subject and subject.compreface_subject_id:
        face_thumbnail_path = _find_first_thumbnail(subject.compreface_subject_id)

    face_summary = {
        "enrolled": enrolled,
        "sample_count": sample_count or 0,
        "face_thumbnail_path": face_thumbnail_path,
    }

    # ---- DerivedBadges from snapshot columns (NULL passthrough) -------------
    derived_badges = {
        "tier": contact.tier,
        "is_active": contact.is_active,
        "is_regular": contact.is_regular,
        "is_connected": contact.is_connected,
    }

    return {
        "contact": contact,
        "display_name": _display_name(contact),
        "contact_reference_chips": list(ref_chips.values()),
        "face_summary": face_summary,
        "derived_badges": derived_badges,
    }


async def create_contact(
    db: AsyncSession,
    payload: Any,
    actor_id: Optional[int],
) -> tuple[Contact, list[str]]:
    """Create a new Contact.

    payload is a ContactCreate Pydantic model.
    Returns (contact, warnings) where warnings is a list of non-blocking strings.
    On duplicate email with an existing non-deleted contact, appends a warning
    but does NOT raise.
    """
    warnings: list[str] = []

    # Validate + coerce custom_data
    raw_custom = payload.custom_data if hasattr(payload, "custom_data") else {}
    coerced_custom = await validate_and_coerce(db, "contact", raw_custom)

    # Duplicate-email check (non-blocking)
    if payload.email:
        dup_result = await db.execute(
            select(func.count()).select_from(Contact).where(
                Contact.email == payload.email,
                Contact.is_deleted.is_(False),
            )
        )
        dup_count = dup_result.scalar_one()
        if dup_count > 0:
            warnings.append(
                f"A contact with email '{payload.email}' already exists."
            )

    contact = Contact(
        contact_type=payload.contact_type,
        contact_subtype=getattr(payload, "contact_subtype", None),
        first_name=payload.first_name or "",
        last_name=payload.last_name or "",
        nickname=getattr(payload, "nickname", None),
        suffix=getattr(payload, "suffix", None),
        gender=getattr(payload, "gender", None),
        birth_date=getattr(payload, "birth_date", None),
        phone=getattr(payload, "phone", None),
        email=getattr(payload, "email", None),
        street_address=getattr(payload, "street_address", None),
        external_id=getattr(payload, "external_id", None),
        custom_data=coerced_custom,
        is_deleted=False,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(contact)
    await db.flush()

    after_dict = _contact_to_audit_dict(contact)
    await audit_svc.record(
        db, actor_id, "contact.create", "contact", contact.id, None, after_dict
    )

    await db.commit()
    await db.refresh(contact)
    return contact, warnings


async def update_contact(
    db: AsyncSession,
    contact_id: int,
    payload: Any,
    actor_id: Optional[int],
) -> Contact:
    """Update a contact (partial update via model_dump(exclude_unset=True)).

    external_id is immutable — silently ignored even if present in payload.
    Merges existing custom_data with incoming custom_data before validation.
    """
    result = await db.execute(
        select(Contact).where(Contact.id == contact_id).with_for_update()
    )
    contact = result.scalar_one_or_none()
    if contact is None or contact.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contact {contact_id} not found",
        )

    before_dict = _contact_to_audit_dict(contact)

    updates: dict[str, Any] = payload.model_dump(exclude_unset=True)

    # external_id is immutable — always ignore
    updates.pop("external_id", None)

    # Handle custom_data merge + validation
    incoming_custom: Optional[dict[str, Any]] = updates.pop("custom_data", None)
    if incoming_custom is not None:
        merged_custom = {**(contact.custom_data or {}), **incoming_custom}
        coerced_custom = await validate_and_coerce(db, "contact", merged_custom)
        contact.custom_data = coerced_custom

    # Apply core field changes
    for field, value in updates.items():
        if field in _ASSIGNABLE_CORE_FIELDS:
            setattr(contact, field, value)

    contact.updated_at = utc_now()
    await db.flush()

    after_dict = _contact_to_audit_dict(contact)
    await audit_svc.record(
        db, actor_id, "contact.update", "contact", contact.id, before_dict, after_dict
    )

    await db.commit()
    await db.refresh(contact)
    return contact


async def soft_delete_contact(
    db: AsyncSession,
    contact_id: int,
    actor_id: Optional[int],
) -> None:
    """Soft-delete a contact (idempotent).

    If already deleted, returns without writing a second audit row.
    Does NOT delete or cascade Participant rows.
    """
    result = await db.execute(
        select(Contact).where(Contact.id == contact_id)
    )
    contact = result.scalar_one_or_none()
    if contact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contact {contact_id} not found",
        )

    # Idempotent: already deleted → no-op
    if contact.is_deleted:
        return

    before_dict = _contact_to_audit_dict(contact)
    contact.is_deleted = True
    contact.updated_at = utc_now()
    await db.flush()

    after_dict = _contact_to_audit_dict(contact)
    await audit_svc.record(
        db, actor_id, "contact.delete", "contact", contact.id, before_dict, after_dict
    )

    await db.commit()


async def restore_contact(
    db: AsyncSession,
    contact_id: int,
    actor_id: Optional[int],
) -> Contact:
    """Restore a soft-deleted contact.

    Raises HTTPException(409) if the contact is NOT currently deleted.
    Raises HTTPException(404) if the contact does not exist.
    """
    result = await db.execute(
        select(Contact).where(Contact.id == contact_id)
    )
    contact = result.scalar_one_or_none()
    if contact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contact {contact_id} not found",
        )

    if not contact.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Contact {contact_id} is not deleted and cannot be restored",
        )

    before_dict = _contact_to_audit_dict(contact)
    contact.is_deleted = False
    contact.updated_at = utc_now()
    await db.flush()

    after_dict = _contact_to_audit_dict(contact)
    await audit_svc.record(
        db, actor_id, "contact.restore", "contact", contact.id, before_dict, after_dict
    )

    await db.commit()
    await db.refresh(contact)
    return contact


async def list_contact_attendance(
    db: AsyncSession,
    contact_id: int,
    *,
    page: int = 1,
    page_size: int = 25,
    source: Optional[str] = None,
    event_type: Optional[str] = None,
) -> dict[str, Any]:
    """List attendance history for a contact.

    Raises 404 if contact is missing.
    event_type filter is tolerated even if Event has no event_type column yet
    (guard with hasattr — Event.event_type is an S04 addition).
    """
    # Verify contact exists
    contact_result = await db.execute(
        select(Contact.id).where(Contact.id == contact_id)
    )
    if contact_result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Contact {contact_id} not found",
        )

    page = max(page, 1)
    page_size = max(1, min(page_size, 100))

    # Build base conditions
    conditions = [Participant.contact_id == contact_id]
    if source is not None:
        conditions.append(Participant.source == source)

    # event_type guard: Event may not have event_type column yet (S04)
    apply_event_type_filter = (
        event_type is not None and hasattr(Event, "event_type")
    )

    # Count query
    count_stmt = (
        select(func.count())
        .select_from(Participant)
        .join(Event, Participant.event_id == Event.id)
        .where(*conditions)
    )
    if apply_event_type_filter:
        count_stmt = count_stmt.where(
            getattr(Event, "event_type") == event_type
        )
    count_result = await db.execute(count_stmt)
    total: int = count_result.scalar_one()

    # Data query
    offset = (page - 1) * page_size
    items_stmt = (
        select(Participant, Event)
        .join(Event, Participant.event_id == Event.id)
        .where(*conditions)
        .order_by(Event.start_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    if apply_event_type_filter:
        items_stmt = items_stmt.where(
            getattr(Event, "event_type") == event_type
        )
    items_result = await db.execute(items_stmt)
    rows = items_result.all()

    items = []
    for participant, event in rows:
        item: dict[str, Any] = {
            "participant_id": participant.id,
            "event_id": event.id,
            "event_title": event.title,
            "start_at": event.start_at,
            "status": participant.status,
            "source": participant.source,
            "role": participant.role,
            "created_at": participant.created_at,
        }
        if hasattr(Event, "event_type"):
            item["event_type"] = getattr(event, "event_type", None)
        items.append(item)

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items,
    }
