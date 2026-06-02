from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin, require_volunteer
from app.models import CiviCRMMember, ComprefaceSubject
from app.schemas import AttendeeResponse, MemberResponse
from app.services.civicrm import CiviCRMClient

router = APIRouter(prefix="/members", tags=["members"])


@router.get("", response_model=list[MemberResponse])
async def search_members(
    search: str = "",
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Search members by first name, last name, or email."""
    if not search.strip():
        result = await db.execute(
            select(CiviCRMMember).limit(limit)
        )
        return result.scalars().all()

    q = f"%{search}%"
    result = await db.execute(
        select(CiviCRMMember)
        .where(
            or_(
                CiviCRMMember.first_name.ilike(q),
                CiviCRMMember.last_name.ilike(q),
                CiviCRMMember.email.ilike(q),
            )
        )
        .limit(limit)
    )
    return result.scalars().all()


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
    """List all CiviCRM contacts with their enrolled face thumbnails."""
    query = (
        select(CiviCRMMember, ComprefaceSubject)
        .outerjoin(
            ComprefaceSubject,
            CiviCRMMember.contact_id == ComprefaceSubject.contact_id,
        )
        .order_by(CiviCRMMember.last_name, CiviCRMMember.first_name)
    )

    if search.strip():
        q = f"%{search}%"
        query = query.where(
            or_(
                CiviCRMMember.first_name.ilike(q),
                CiviCRMMember.last_name.ilike(q),
                CiviCRMMember.nickname.ilike(q),
                CiviCRMMember.email.ilike(q),
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
                contact_id=member.contact_id,
                first_name=member.first_name,
                last_name=member.last_name,
                email=member.email,
                nickname=member.nickname,
                face_thumbnail_path=face_thumbnail_path,
                sample_count=sample_count,
            )
        )

    return attendees


@router.post("/sync")
async def sync_members(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Force sync members from CiviCRM."""
    try:
        client = CiviCRMClient()
        members = await client.sync_members()
        synced = 0
        for member_data in members:
            contact_id = int(member_data.get("id", 0))
            if not contact_id:
                continue
            existing = await db.get(CiviCRMMember, contact_id)
            if existing:
                existing.first_name = member_data.get("first_name", existing.first_name)
                existing.last_name = member_data.get("last_name", existing.last_name)
                existing.nickname = member_data.get("nick_name", existing.nickname)
                existing.email = member_data.get("email", existing.email)
                existing.last_synced_at = datetime.now(timezone.utc).replace(tzinfo=None)
            else:
                new_member = CiviCRMMember(
                    contact_id=contact_id,
                    first_name=member_data.get("first_name", ""),
                    last_name=member_data.get("last_name", ""),
                    nickname=member_data.get("nick_name"),
                    email=member_data.get("email"),
                    last_synced_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
                db.add(new_member)
            synced += 1
        await db.commit()
        await client.close()
        return {"message": f"Synced {synced} members", "synced_count": synced}
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"CiviCRM not configured: {exc}"
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="CiviCRM sync failed. Check server logs.",
        )
