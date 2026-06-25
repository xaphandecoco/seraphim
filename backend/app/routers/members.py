"""Members router — /members prefix.

Route declaration order (FastAPI matches in declaration order):
  GET    ''                  — paginated list / search
  GET    '/attendees'        — S07 dependency; byte-for-byte retained
  POST   ''                  — create contact
  GET    '/{contact_id}'     — detail
  PATCH  '/{contact_id}'     — partial update
  DELETE '/{contact_id}'     — soft delete
  POST   '/{contact_id}/restore'     — admin restore
  GET    '/{contact_id}/attendance'  — attendance history

The literal '/attendees' route is declared BEFORE the parameterized '/{contact_id}'
to prevent FastAPI capturing 'attendees' as a contact_id value (recon risk #5).
"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin, require_volunteer
from app.models import ComprefaceSubject, Contact
from app.schemas import (
    AttendeeResponse,
    ContactAttendanceItem,
    ContactCreate,
    ContactDetailResponse,
    ContactUpdate,
    DerivedBadges,
    FaceSummary,
    PaginatedAttendanceResponse,
    PaginatedContactResponse,
)
from app.services import contact_service

router = APIRouter(prefix="/members", tags=["members"])


# ============================================================================
# GET '' — paginated list / search
# ============================================================================

@router.get("", response_model=PaginatedContactResponse)
async def list_members(
    search: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    contact_type: Optional[str] = None,
    subtype: Optional[str] = None,
    tier: Optional[str] = None,
    is_regular: Optional[bool] = None,
    include_deleted: bool = False,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Paginated contact search.

    include_deleted is honored ONLY for admin callers — volunteers always see
    non-deleted rows regardless of the value they pass.
    The search includes nickname (P05 fix).
    """
    # Gate include_deleted: only admins may see deleted rows
    effective_include_deleted = include_deleted and user["role"] == "admin"

    result = await contact_service.list_contacts(
        db,
        search=search,
        page=page,
        page_size=page_size,
        contact_type=contact_type,
        contact_subtype=subtype,
        tier=tier,
        is_regular=is_regular,
        include_deleted=effective_include_deleted,
    )
    # contact_service.list_contacts returns a dict {total, page, page_size, items}
    # where items is a list of Contact ORM rows.
    # ContactListItem requires display_name (computed) which doesn't exist on the ORM
    # row — build each item explicitly.
    from app.schemas import ContactListItem

    items = [
        ContactListItem(
            id=c.id,
            display_name=contact_service._display_name(c),
            first_name=c.first_name,
            last_name=c.last_name,
            nickname=c.nickname,
            email=c.email,
            phone=c.phone,
            contact_type=c.contact_type,
            contact_subtype=c.contact_subtype,
            tier=c.tier,
            is_regular=c.is_regular,
            is_connected=c.is_connected,
            face_thumbnail_path=None,  # list view omits thumbnail (detail only)
        )
        for c in result["items"]
    ]
    return PaginatedContactResponse(
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
        items=items,
    )


# ============================================================================
# GET '/attendees' — S07 dependency; retained byte-for-byte
# ============================================================================

def _find_first_thumbnail(subject_id: str, base_path: str | None = None) -> Optional[str]:
    """Return the /storage/-prefixed URL of the first enrolled thumbnail for a subject."""
    import os
    from app.config import legacy_settings as _ls
    if base_path is None:
        base_path = os.environ.get("STORAGE_PATH", _ls.STORAGE_PATH)
    enrolled_dir = Path(base_path) / "enrolled" / subject_id
    if not enrolled_dir.exists():
        return None
    thumbs = sorted(enrolled_dir.glob("sample_*_thumb.jpg"))
    if not thumbs:
        return None
    return f"/storage/enrolled/{subject_id}/{thumbs[0].name}"


@router.get("/attendees", response_model=list[AttendeeResponse])
async def list_attendees(
    search: str = "",
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """List all contacts with their enrolled face thumbnails."""
    query = (
        select(Contact, ComprefaceSubject)
        .outerjoin(
            ComprefaceSubject,
            Contact.id == ComprefaceSubject.contact_id,
        )
        .order_by(Contact.last_name, Contact.first_name)
    )

    if search.strip():
        q = f"%{search}%"
        query = query.where(
            or_(
                Contact.first_name.ilike(q),
                Contact.last_name.ilike(q),
                Contact.nickname.ilike(q),
                Contact.email.ilike(q),
            )
        )

    query = query.limit(limit)
    result = await db.execute(query)
    rows = result.all()

    attendees: list[AttendeeResponse] = []
    for member, subject in rows:
        face_thumbnail_path: Optional[str] = None
        sample_count = 0
        if subject and subject.compreface_subject_id:
            sample_count = subject.sample_count or 0
            face_thumbnail_path = _find_first_thumbnail(subject.compreface_subject_id)

        attendees.append(
            AttendeeResponse(
                contact_id=member.id,
                first_name=member.first_name,
                last_name=member.last_name,
                email=member.email,
                nickname=member.nickname,
                face_thumbnail_path=face_thumbnail_path,
                sample_count=sample_count,
            )
        )

    return attendees


# ============================================================================
# POST '' — create contact
# ============================================================================

@router.post("", response_model=ContactDetailResponse, status_code=201)
async def create_member(
    body: ContactCreate,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Create a new contact.

    The structured 422 from validate_and_coerce bubbles unchanged.
    The response includes a warnings list for non-blocking issues.
    """
    actor_id = int(user["sub"])
    contact, warnings = await contact_service.create_contact(db, body, actor_id)
    # get_contact_detail returns enrichment; use it to build the full response
    detail = await contact_service.get_contact_detail(
        db, contact.id, allow_deleted=False
    )
    return _build_detail_response(detail, warnings=warnings)


# ============================================================================
# GET '/{contact_id}' — detail
# ============================================================================

@router.get("/{contact_id}", response_model=ContactDetailResponse)
async def get_member(
    contact_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Get contact detail.

    Non-admin callers receive 404 for soft-deleted contacts.
    Admin callers see soft-deleted contacts (200).
    """
    allow_deleted = user["role"] == "admin"
    detail = await contact_service.get_contact_detail(
        db, contact_id, allow_deleted=allow_deleted
    )
    return _build_detail_response(detail)


# ============================================================================
# PATCH '/{contact_id}' — partial update
# ============================================================================

@router.patch("/{contact_id}", response_model=ContactDetailResponse)
async def update_member(
    contact_id: int,
    body: ContactUpdate,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Partial update of a contact.

    external_id in the body is ignored — the stored external_id is never mutated.
    """
    actor_id = int(user["sub"])
    await contact_service.update_contact(db, contact_id, body, actor_id)
    detail = await contact_service.get_contact_detail(
        db, contact_id, allow_deleted=False
    )
    return _build_detail_response(detail)


# ============================================================================
# DELETE '/{contact_id}' — soft delete (idempotent)
# ============================================================================

@router.delete("/{contact_id}", status_code=204)
async def delete_member(
    contact_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Soft-delete a contact. Idempotent — second call still returns 204."""
    actor_id = int(user["sub"])
    await contact_service.soft_delete_contact(db, contact_id, actor_id)
    return Response(status_code=204)


# ============================================================================
# POST '/{contact_id}/restore' — admin restore
# ============================================================================

@router.post("/{contact_id}/restore", response_model=ContactDetailResponse)
async def restore_member(
    contact_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Restore a soft-deleted contact.

    Returns 409 when the contact is not currently deleted.
    Requires admin role.
    """
    actor_id = int(user["sub"])
    contact = await contact_service.restore_contact(db, contact_id, actor_id)
    detail = await contact_service.get_contact_detail(
        db, contact.id, allow_deleted=True
    )
    return _build_detail_response(detail)


# ============================================================================
# GET '/{contact_id}/attendance' — attendance history
# ============================================================================

@router.get("/{contact_id}/attendance", response_model=PaginatedAttendanceResponse)
async def get_member_attendance(
    contact_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    source: Optional[str] = None,
    event_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Paginated attendance history for a contact.

    Returns 404 for unknown contact.
    """
    result = await contact_service.list_contact_attendance(
        db,
        contact_id,
        page=page,
        page_size=page_size,
        source=source,
        event_type=event_type,
    )
    # contact_service.list_contact_attendance returns a dict with raw item dicts
    # matching the ContactAttendanceItem schema fields.
    items = [
        ContactAttendanceItem(
            participant_id=item["participant_id"],
            event_id=item["event_id"],
            event_title=item.get("event_title") or "",
            start_at=item.get("start_at"),
            status=item["status"],
            source=item["source"],
            role=item.get("role"),
            created_at=item["created_at"],
            event_type=item.get("event_type"),
        )
        for item in result["items"]
    ]
    return PaginatedAttendanceResponse(
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
        items=items,
    )


# ============================================================================
# Internal helpers
# ============================================================================

def _build_detail_response(
    detail: dict,
    warnings: list[str] | None = None,
) -> ContactDetailResponse:
    """Build a ContactDetailResponse from the service dict returned by get_contact_detail.

    detail keys: contact, display_name, contact_reference_chips, face_summary,
                 derived_badges.
    """
    contact = detail["contact"]
    face_summary_dict = detail.get("face_summary", {})
    derived_badges_dict = detail.get("derived_badges", {})

    return ContactDetailResponse(
        id=contact.id,
        display_name=detail.get("display_name", ""),
        external_id=contact.external_id,
        contact_type=contact.contact_type,
        contact_subtype=contact.contact_subtype,
        first_name=contact.first_name,
        last_name=contact.last_name,
        nickname=contact.nickname,
        suffix=contact.suffix,
        gender=contact.gender,
        birth_date=contact.birth_date,
        phone=contact.phone,
        email=contact.email,
        street_address=contact.street_address,
        custom_data=contact.custom_data or {},
        is_deleted=contact.is_deleted,
        created_at=contact.created_at,
        updated_at=contact.updated_at,
        last_attended_at=contact.last_attended_at,
        attendance_count=contact.attendance_count,
        weeks_absent=contact.weeks_absent,
        contact_reference_chips=detail.get("contact_reference_chips", []),
        face_summary=FaceSummary(
            enrolled=face_summary_dict.get("enrolled", False),
            sample_count=face_summary_dict.get("sample_count", 0),
            face_thumbnail_path=face_summary_dict.get("face_thumbnail_path"),
        ),
        derived_badges=DerivedBadges(
            tier=derived_badges_dict.get("tier"),
            is_active=derived_badges_dict.get("is_active"),
            is_regular=derived_badges_dict.get("is_regular"),
            is_connected=derived_badges_dict.get("is_connected"),
        ),
        warnings=warnings or [],
    )
