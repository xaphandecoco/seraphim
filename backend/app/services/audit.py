"""Audit log write helper.

Ownership: S02 (per CN-03/CN-25).
The `audit_log` table is created by S01; this module is the sole writer.

Circular-import rule: this module imports ONLY from app.models and SQLAlchemy.
It must NEVER import from any router module. All routers import this module;
this module never imports a router.
"""
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, utc_now


async def record(
    db: AsyncSession,
    actor_id: Optional[int],
    action: str,
    entity: str,
    entity_id: Optional[int],
    before: Optional[dict[str, Any]],
    after: Optional[dict[str, Any]],
) -> None:
    """Write one audit_log row.

    Called after the primary mutation is flushed (via db.flush()) but before
    commit — both are committed together by the caller.

    actor_id=None signals a system/background action.
    """
    log = AuditLog(
        actor_id=actor_id,
        action=action,
        entity=entity,
        entity_id=entity_id,
        before=before,
        after=after,
        created_at=utc_now(),
    )
    db.add(log)
    # Caller is responsible for db.commit() — record() does not commit.
