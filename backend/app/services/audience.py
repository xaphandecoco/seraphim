"""Audience resolver service.

Ownership: S05-F04.
Consumed by: S06, S09, S10, S14 — keep signatures exact.

resolve_audience(db, audience) -> Select of Contact.id
count_audience(db, audience) -> int

AudienceSelector schema (caller-supplied Pydantic model or plain dict):
    mode: Literal['all', 'ids', 'group', 'saved_search']
    include_deleted: bool = False
    ids: list[int] | None               # for mode='ids'
    group_id: int | None                # for mode='group'
    saved_search_id: int | None         # for mode='saved_search'
    group_type: Literal['static','smart'] | None  # for mode='group'

Group/GroupMember/SavedSearch models are owned by S09 and DO NOT EXIST yet.
They are imported lazily inside the relevant mode branches so this module
remains importable before S09 ships.  If the import fails the affected
branch raises HTTPException(400) with a clear message.

Similarly, criteria_compiler (S09) is guarded: ImportError -> 400.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from sqlalchemy.sql.selectable import Select


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _base_select(include_deleted: bool) -> "Select":
    """Return a Select of Contact.id with the soft-delete guard applied."""
    from app.models import Contact

    stmt = select(Contact.id)
    if not include_deleted:
        stmt = stmt.where(Contact.is_deleted == False)  # noqa: E712 — SQLAlchemy requires ==
    return stmt


def _compile_criteria(criteria: Any) -> "Select":
    """Delegate to the S09 criteria compiler.

    Raises HTTPException(400) when the compiler is not yet installed.
    """
    try:
        from app.services.criteria_compiler import compile_contact_criteria
    except ImportError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Smart/saved-search audience resolution requires the S09 "
                "criteria compiler (app.services.criteria_compiler), which is "
                "not yet available."
            ),
        )
    return compile_contact_criteria(criteria)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def resolve_audience(
    db: AsyncSession,
    audience: Any,
) -> "Select":
    """Return a Select of Contact.id matching *audience*.

    Parameters
    ----------
    db:
        Active async SQLAlchemy session.
    audience:
        Any object (Pydantic model or plain namespace) exposing at minimum:
            .mode              str
            .include_deleted   bool   (default False)
        and mode-specific attributes:
            .ids               list[int] | None
            .group_id          int | None
            .group_type        str | None  ('static' | 'smart')
            .saved_search_id   int | None

    Returns
    -------
    Select
        A SQLAlchemy Select statement over Contact.id.  The caller may
        further filter, join, or wrap in a subquery.
    """
    mode: str = getattr(audience, "mode", "all")
    include_deleted: bool = bool(getattr(audience, "include_deleted", False))

    base = _base_select(include_deleted)

    # ------------------------------------------------------------------
    # mode='all'  — every (non-deleted) contact
    # ------------------------------------------------------------------
    if mode == "all":
        return base

    # ------------------------------------------------------------------
    # mode='ids'  — caller-supplied list; unknowns dropped silently
    # ------------------------------------------------------------------
    if mode == "ids":
        from app.models import Contact

        raw_ids: list[int] = list(getattr(audience, "ids", None) or [])
        if not raw_ids:
            # Return a statement that produces zero rows
            return base.where(Contact.id.in_([]))

        # Validate against real contacts (drops unknowns silently)
        stmt = base.where(Contact.id.in_(raw_ids))
        return stmt

    # ------------------------------------------------------------------
    # mode='group'  — static or smart group (S09 models)
    # ------------------------------------------------------------------
    if mode == "group":
        group_id: int | None = getattr(audience, "group_id", None)
        if group_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="audience.group_id is required for mode='group'",
            )

        # Lazy import — Group and GroupMember are owned by S09.
        # Catch both ImportError (name absent from module) and NameError
        # (CPython may bind earlier names in a multi-name import before
        # raising; guard against any residual reference error).
        try:
            from app.models import Contact, Group, GroupMember  # type: ignore[attr-defined]
        except (ImportError, NameError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Group audience resolution requires the S09 Group/GroupMember "
                    "models, which are not yet available."
                ),
            )

        group = await db.get(Group, group_id)
        if group is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Group {group_id} not found",
            )

        group_type: str = getattr(audience, "group_type", None) or getattr(
            group, "group_type", "static"
        )

        if group_type == "static":
            # Join Contact.id through GroupMember
            stmt = (
                select(Contact.id)
                .join(GroupMember, GroupMember.contact_id == Contact.id)
                .where(GroupMember.group_id == group_id)
            )
            if not include_deleted:
                stmt = stmt.where(Contact.is_deleted == False)  # noqa: E712
            return stmt

        # smart group — delegate to criteria compiler
        criteria = getattr(group, "criteria", None)
        return _compile_criteria(criteria)

    # ------------------------------------------------------------------
    # mode='saved_search'  — persisted search criteria (S09 models)
    # ------------------------------------------------------------------
    if mode == "saved_search":
        saved_search_id: int | None = getattr(audience, "saved_search_id", None)
        if saved_search_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="audience.saved_search_id is required for mode='saved_search'",
            )

        # Lazy import — SavedSearch is owned by S09.
        # Catch both ImportError and NameError for the same reason as the
        # group branch above.
        try:
            from app.models import SavedSearch  # type: ignore[attr-defined]
        except (ImportError, NameError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Saved-search audience resolution requires the S09 SavedSearch "
                    "model, which is not yet available."
                ),
            )

        saved_search = await db.get(SavedSearch, saved_search_id)
        if saved_search is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"SavedSearch {saved_search_id} not found",
            )

        criteria = getattr(saved_search, "criteria", None)
        return _compile_criteria(criteria)

    # ------------------------------------------------------------------
    # Unknown mode
    # ------------------------------------------------------------------
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unknown audience mode: {mode!r}. Expected one of: all, ids, group, saved_search",
    )


async def count_audience(
    db: AsyncSession,
    audience: Any,
) -> int:
    """Return the number of contacts matching *audience*.

    Executes COUNT(*) over resolve_audience(...).subquery().
    """
    inner = await resolve_audience(db, audience)
    count_stmt = select(func.count()).select_from(inner.subquery())
    result = await db.execute(count_stmt)
    return result.scalar_one()
