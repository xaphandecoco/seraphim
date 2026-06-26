"""Name-aliases CRUD router (S22-F05).

Prefix: /name-aliases

Endpoints:
  GET    /           — admin, search + paginate
  POST   /           — admin, create alias (normalize before store)
  POST   /teach      — volunteer, teach alias from a review-queue resolution
  PATCH  /{id}       — admin, swap contact_id (update alias)
  DELETE /{id}       — admin, hard delete, 204

Circular-import rule: this module must NEVER import from any router module.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status as http_status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin, require_volunteer
from app.models import Contact, NameAlias, NameMatchReviewQueue, utc_now
from app.services import audit as audit_svc
from app.services.name_match import normalize_name, teach_alias_from_resolution

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/name-aliases", tags=["name-aliases"])


# ---------------------------------------------------------------------------
# Pydantic schemas local to this router
# ---------------------------------------------------------------------------


class NameAliasCreateRequest(BaseModel):
    alias_text: str = Field(min_length=1, max_length=255)
    contact_id: int
    alias_type: Optional[str] = "nick"
    source: Optional[str] = "admin"


class NameAliasPatchRequest(BaseModel):
    """PATCH body — only contact_id swap is supported per spec."""
    contact_id: int


class NameAliasTeachRequest(BaseModel):
    """POST /teach body — derive alias from a resolved review-queue item."""
    review_queue_id: int
    contact_id: int
    alias_text: Optional[str] = None


class NameAliasRow(BaseModel):
    id: int
    alias_text: str
    alias_type: str
    contact_id: int
    source: str
    contact_display_name: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class PaginatedNameAliasResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: List[NameAliasRow]


# ---------------------------------------------------------------------------
# GET /name-aliases
# ---------------------------------------------------------------------------


@router.get("", response_model=PaginatedNameAliasResponse)
async def list_name_aliases(
    search: str = Query("", description="Partial match on alias_text"),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    contact_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Paginated list of name aliases. Admin only.

    Optional filters: search (alias_text ILIKE), contact_id.
    """
    page_size = min(page_size, 100)
    offset = (page - 1) * page_size

    base_query = select(NameAlias)

    if search.strip():
        base_query = base_query.where(
            NameAlias.alias_text.ilike(f"%{search.strip()}%")
        )
    if contact_id is not None:
        base_query = base_query.where(NameAlias.contact_id == contact_id)

    # Count
    count_query = select(func.count()).select_from(base_query.subquery())
    total: int = (await db.execute(count_query)).scalar_one()

    # Page
    rows = (
        await db.execute(
            base_query
            .order_by(NameAlias.alias_text.asc())
            .offset(offset)
            .limit(page_size)
        )
    ).scalars().all()

    # Batch load contacts for display names
    cids = {r.contact_id for r in rows}
    contacts_by_id: dict[int, Contact] = {}
    if cids:
        ct_rows = (
            await db.execute(select(Contact).where(Contact.id.in_(cids)))
        ).scalars().all()
        contacts_by_id = {ct.id: ct for ct in ct_rows}

    items: List[NameAliasRow] = []
    for row in rows:
        contact_display_name: Optional[str] = None
        ct = contacts_by_id.get(row.contact_id)
        if ct is not None:
            contact_display_name = f"{ct.first_name} {ct.last_name}".strip()

        items.append(NameAliasRow(
            id=row.id,
            alias_text=row.alias_text,
            alias_type=row.alias_type,
            contact_id=row.contact_id,
            source=row.source,
            contact_display_name=contact_display_name,
            created_at=row.created_at.isoformat() if row.created_at else None,
            updated_at=row.updated_at.isoformat() if row.updated_at else None,
        ))

    return PaginatedNameAliasResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=items,
    )


# ---------------------------------------------------------------------------
# POST /name-aliases
# ---------------------------------------------------------------------------


@router.post("", response_model=NameAliasRow, status_code=http_status.HTTP_201_CREATED)
async def create_name_alias(
    body: NameAliasCreateRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Create a new name alias. Admin only.

    alias_text is normalized (lowercase, diacritics stripped, honorifics removed)
    before storage. Returns 409 if the normalized alias_text already exists.
    Returns 400 if contact not found or deleted.
    """
    actor_id = int(user["sub"])

    # Normalize alias_text before storage
    normalized_text = normalize_name(body.alias_text)
    if not normalized_text:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="alias_text is empty after normalization.",
        )

    # Validate contact exists and is not deleted
    contact = await db.get(Contact, body.contact_id)
    if contact is None:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Contact {body.contact_id} not found.",
        )
    if contact.is_deleted:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Contact {body.contact_id} is deleted.",
        )

    # Check for duplicate alias_text (unique constraint)
    existing = (
        await db.execute(
            select(NameAlias).where(NameAlias.alias_text == normalized_text)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=f"Alias '{normalized_text}' already exists (id={existing.id}).",
        )

    alias_type = body.alias_type or "nick"
    source = body.source or "admin"
    now = utc_now()

    alias = NameAlias(
        alias_text=normalized_text,
        alias_type=alias_type,
        contact_id=body.contact_id,
        created_by_id=actor_id,
        source=source,
        meta={},
        created_at=now,
        updated_at=now,
    )
    db.add(alias)
    await db.flush()

    try:
        await audit_svc.record(
            db,
            actor_id=actor_id,
            action="name_alias.created",
            entity="name_alias",
            entity_id=alias.id,
            before=None,
            after={
                "alias_text": normalized_text,
                "alias_type": alias_type,
                "contact_id": body.contact_id,
                "source": source,
            },
        )
        await db.commit()
    except Exception:
        try:
            await db.commit()
        except Exception:
            pass

    display_name = f"{contact.first_name} {contact.last_name}".strip()
    return NameAliasRow(
        id=alias.id,
        alias_text=alias.alias_text,
        alias_type=alias.alias_type,
        contact_id=alias.contact_id,
        source=alias.source,
        contact_display_name=display_name,
        created_at=alias.created_at.isoformat() if alias.created_at else None,
        updated_at=alias.updated_at.isoformat() if alias.updated_at else None,
    )


# ---------------------------------------------------------------------------
# POST /name-aliases/teach  (declared BEFORE /{id} to avoid routing collision)
# ---------------------------------------------------------------------------


@router.post("/teach", response_model=NameAliasRow, status_code=http_status.HTTP_200_OK)
async def teach_alias(
    body: NameAliasTeachRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_volunteer),
):
    """Teach the matcher a new alias from a resolved review-queue item.

    Volunteer+ access. Wraps teach_alias_from_resolution from the service layer.
    Upsert-safe: ON CONFLICT(alias_text) DO UPDATE SET contact_id.

    - 404 if review_queue_id not found.
    - 400 if contact not found or deleted.
    """
    actor_id = int(user["sub"])

    # Validate review queue row exists
    queue_row = await db.get(NameMatchReviewQueue, body.review_queue_id)
    if queue_row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Review queue item {body.review_queue_id} not found.",
        )

    # Validate contact exists and is not deleted
    contact = await db.get(Contact, body.contact_id)
    if contact is None:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Contact {body.contact_id} not found.",
        )
    if contact.is_deleted:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Contact {body.contact_id} is deleted.",
        )

    try:
        alias = await teach_alias_from_resolution(
            db=db,
            review_queue_id=body.review_queue_id,
            contact_id=body.contact_id,
            alias_text=body.alias_text or None,
            actor_id=actor_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    display_name = f"{contact.first_name} {contact.last_name}".strip()
    return NameAliasRow(
        id=alias.id,
        alias_text=alias.alias_text,
        alias_type=alias.alias_type,
        contact_id=alias.contact_id,
        source=alias.source,
        contact_display_name=display_name,
        created_at=alias.created_at.isoformat() if alias.created_at else None,
        updated_at=alias.updated_at.isoformat() if alias.updated_at else None,
    )


# ---------------------------------------------------------------------------
# PATCH /name-aliases/{alias_id}
# ---------------------------------------------------------------------------


@router.patch("/{alias_id}", response_model=NameAliasRow)
async def update_name_alias(
    alias_id: int,
    body: NameAliasPatchRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Swap the contact_id on an alias. Admin only.

    - 404 if alias not found.
    - 400 if new contact not found or deleted.
    """
    actor_id = int(user["sub"])

    alias = await db.get(NameAlias, alias_id)
    if alias is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"NameAlias {alias_id} not found.",
        )

    # Validate new contact
    contact = await db.get(Contact, body.contact_id)
    if contact is None:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Contact {body.contact_id} not found.",
        )
    if contact.is_deleted:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Contact {body.contact_id} is deleted.",
        )

    before = {"contact_id": alias.contact_id}

    alias.contact_id = body.contact_id
    alias.updated_at = utc_now()

    await db.flush()

    try:
        await audit_svc.record(
            db,
            actor_id=actor_id,
            action="name_alias.updated",
            entity="name_alias",
            entity_id=alias_id,
            before=before,
            after={"contact_id": body.contact_id},
        )
        await db.commit()
    except Exception:
        try:
            await db.commit()
        except Exception:
            pass

    display_name = f"{contact.first_name} {contact.last_name}".strip()
    return NameAliasRow(
        id=alias.id,
        alias_text=alias.alias_text,
        alias_type=alias.alias_type,
        contact_id=alias.contact_id,
        source=alias.source,
        contact_display_name=display_name,
        created_at=alias.created_at.isoformat() if alias.created_at else None,
        updated_at=alias.updated_at.isoformat() if alias.updated_at else None,
    )


# ---------------------------------------------------------------------------
# DELETE /name-aliases/{alias_id}
# ---------------------------------------------------------------------------


@router.delete("/{alias_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_name_alias(
    alias_id: int,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_admin),
):
    """Hard-delete a name alias. Admin only. Returns 204 No Content."""
    actor_id = int(user["sub"])

    alias = await db.get(NameAlias, alias_id)
    if alias is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"NameAlias {alias_id} not found.",
        )

    before = {
        "alias_text": alias.alias_text,
        "contact_id": alias.contact_id,
    }

    await db.delete(alias)

    try:
        await audit_svc.record(
            db,
            actor_id=actor_id,
            action="name_alias.deleted",
            entity="name_alias",
            entity_id=alias_id,
            before=before,
            after=None,
        )
        await db.commit()
    except Exception:
        try:
            await db.commit()
        except Exception:
            pass

    return Response(status_code=http_status.HTTP_204_NO_CONTENT)
