"""Row classification for the import wizard (S10).

Public API
----------
classify(mapped, match_key, db) -> (kind, existing_contact | None, message)

kind values
-----------
'new'       No matching contact found; import should CREATE a new row.
'match'     Exactly one contact matched; import should UPDATE or SKIP.
'ambiguous' Multiple or name-only-partial match; send to review queue.
'error'     Missing required field or unparseable key; skip row, record error.

match_key values
----------------
'external_id'  SELECT Contact WHERE external_id = mapped.external_id (int-coerced)
'email'        SELECT Contact WHERE email = mapped.email
'name'         Call match_name(raw_name, db, source='import', auto_enqueue=False);
               SINGLE→'match', AMBIGUOUS/UNMATCHED→'ambiguous'.

Circular-import rule (CN-25): must NOT import from any router module.
"""
from __future__ import annotations

from typing import Any, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Contact


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def classify(
    mapped: Any,
    match_key: str,
    db: AsyncSession,
) -> Tuple[str, Optional[Contact], str]:
    """Classify a mapped contact row against the database.

    Parameters
    ----------
    mapped:
        A ``ContactMapped`` dataclass (from migration.mapper).
    match_key:
        One of ``'external_id'``, ``'email'``, ``'name'``.
    db:
        Active async session.

    Returns
    -------
    (kind, existing_contact_or_None, message)
    """
    first_name: str = getattr(mapped, "first_name", None) or ""
    last_name: str = getattr(mapped, "last_name", None) or ""

    if not first_name.strip():
        return ("error", None, "Missing required field: first_name")

    if match_key == "external_id":
        return await _classify_by_external_id(mapped, db)
    elif match_key == "email":
        return await _classify_by_email(mapped, db)
    elif match_key == "name":
        return await _classify_by_name(first_name, last_name, db)
    else:
        return ("error", None, f"Unknown match_key: {match_key!r}")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _classify_by_external_id(
    mapped: Any,
    db: AsyncSession,
) -> Tuple[str, Optional[Contact], str]:
    raw_ext = getattr(mapped, "external_id", None)
    if raw_ext is None:
        return ("error", None, "Missing required field: external_id for match_key='external_id'")

    try:
        ext_id = int(raw_ext)
    except (TypeError, ValueError):
        return ("error", None, f"external_id {raw_ext!r} is not a valid integer")

    result = await db.execute(
        select(Contact).where(Contact.external_id == ext_id)
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        return ("match", existing, f"matched by external_id={ext_id}")
    return ("new", None, "no existing contact found")


async def _classify_by_email(
    mapped: Any,
    db: AsyncSession,
) -> Tuple[str, Optional[Contact], str]:
    email: str = getattr(mapped, "email", None) or ""
    if not email.strip():
        return ("error", None, "Missing required field: email for match_key='email'")

    result = await db.execute(
        select(Contact).where(Contact.email == email.strip())
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        return ("match", existing, f"matched by email={email!r}")
    return ("new", None, "no existing contact found")


async def _classify_by_name(
    first_name: str,
    last_name: str,
    db: AsyncSession,
) -> Tuple[str, Optional[Contact], str]:
    from app.services.name_match import match_name  # guarded against circular import

    raw_name = f"{first_name.strip()} {last_name.strip()}".strip()
    if not raw_name:
        return ("error", None, "Missing required field: name for match_key='name'")

    try:
        match_result = await match_name(
            raw_name=raw_name,
            db=db,
            source="import",
            auto_enqueue=False,
        )
    except Exception as exc:
        return ("error", None, f"name_match error for {raw_name!r}: {exc}")

    outcome = match_result.get("outcome", "UNMATCHED")

    if outcome == "SINGLE":
        contact_id = match_result.get("contact_id")
        if contact_id is None:
            return ("ambiguous", None, f"SINGLE outcome but no contact_id for {raw_name!r}")
        result = await db.execute(
            select(Contact).where(Contact.id == contact_id)
        )
        existing = result.scalar_one_or_none()
        return ("match", existing, f"matched by name: {raw_name!r}")

    return (
        "ambiguous",
        None,
        f"name match outcome={outcome} for {raw_name!r}",
    )
