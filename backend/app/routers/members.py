from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_volunteer
from app.models import Contact, ComprefaceSubject
from app.schemas import AttendeeResponse, MemberResponse

router = APIRouter(prefix="/members", tags=["members"])


@router.get("", response_model=list[MemberResponse])
async def search_members(
    search: str = "",
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Search contacts by first name, last name, or email."""
    limit = min(max(limit, 1), 100)
    if not search.strip():
        result = await db.execute(
            select(Contact).limit(limit)
        )
        members = result.scalars().all()
        return [
            MemberResponse(
                contact_id=m.id,
                first_name=m.first_name,
                last_name=m.last_name,
                email=m.email,
            )
            for m in members
        ]

    q = f"%{search}%"
    result = await db.execute(
        select(Contact)
        .where(
            or_(
                Contact.first_name.ilike(q),
                Contact.last_name.ilike(q),
                Contact.email.ilike(q),
            )
        )
        .limit(limit)
    )
    members = result.scalars().all()
    return [
        MemberResponse(
            contact_id=m.id,
            first_name=m.first_name,
            last_name=m.last_name,
            email=m.email,
        )
        for m in members
    ]


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
