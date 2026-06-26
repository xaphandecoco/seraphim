"""Settings service — business logic for S16 settings / system-status endpoints.

Owns:
  - Fernet encryption helpers for sensitive values
  - System-status probe (postgres, redis, compreface)
  - Job-runs pagination query
  - Config-checklist assembly
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import dynamic_settings
from app.models import AdminSetting, JobRun, Task

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentinel for masked value (user re-submitted without changing)
# ---------------------------------------------------------------------------
MASKED_SENTINEL = "********"


# ---------------------------------------------------------------------------
# Fernet helpers
# ---------------------------------------------------------------------------


def _get_fernet():
    """Return a Fernet instance.  Key is env-var or derived from jwt_secret."""
    from cryptography.fernet import Fernet

    key_env = os.environ.get("SETTINGS_FERNET_KEY", "")
    if key_env:
        raw = key_env.encode()
        # Ensure it's URL-safe base64 — just use it directly if it looks right.
        try:
            return Fernet(raw)
        except Exception:
            pass  # fall through to derive from jwt_secret

    jwt_secret = dynamic_settings.get_jwt_secret()
    if not jwt_secret:
        raise RuntimeError(
            "jwt_secret is not set; cannot derive Fernet key for sensitive settings"
        )
    raw_key = hashlib.sha256(jwt_secret.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(raw_key)
    return Fernet(fernet_key)


def encrypt_value(plaintext: str) -> str:
    """Encrypt a sensitive setting value; returns a URL-safe base64 ciphertext."""
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    """Decrypt a ciphertext returned by encrypt_value."""
    f = _get_fernet()
    return f.decrypt(ciphertext.encode()).decode()


def is_encrypted_value(raw: Any) -> bool:
    """Return True if a raw admin_settings value dict marks itself as encrypted."""
    return isinstance(raw, dict) and raw.get("encrypted") is True


def read_setting_plaintext(setting: AdminSetting) -> Optional[str]:
    """Extract the plaintext value from an AdminSetting row (decrypt if needed)."""
    if setting.value is None:
        return None
    raw = setting.value
    if isinstance(raw, dict) and "value" in raw:
        val = raw["value"]
        if is_encrypted_value(raw) and isinstance(val, str):
            try:
                return decrypt_value(val)
            except Exception:
                logger.warning("Could not decrypt setting %s; returning None", setting.key)
                return None
        return str(val) if val is not None else None
    return None


# ---------------------------------------------------------------------------
# Job-runs query helpers
# ---------------------------------------------------------------------------


async def list_job_runs(
    db: AsyncSession,
    *,
    page: int = 1,
    page_size: int = 20,
    job_name: Optional[str] = None,
) -> tuple[list[dict], int]:
    """Return (items, total) for the job_runs table, ordered started_at DESC.

    duration_ms is computed in Python:
      - finished rows: use stored duration_ms
      - 'running' rows: (now - started_at).ms
      - else: null
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    q = select(JobRun)
    if job_name:
        q = q.where(JobRun.job_name == job_name)

    total_q = select(func.count()).select_from(q.subquery())
    total_result = await db.execute(total_q)
    total = total_result.scalar_one()

    q = q.order_by(JobRun.started_at.desc())
    q = q.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(q)
    rows = result.scalars().all()

    items = []
    for row in rows:
        if row.finished_at is not None:
            duration_ms = row.duration_ms
        elif row.status == "running":
            elapsed = (now - row.started_at).total_seconds()
            duration_ms = int(elapsed * 1000)
        else:
            duration_ms = None

        items.append({
            "id": row.id,
            "job_name": row.job_name,
            "status": row.status,
            "detail": row.detail,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "duration_ms": duration_ms,
        })

    return items, total


async def get_latest_job_status(
    db: AsyncSession,
    job_names: list[str],
) -> dict[str, dict]:
    """For each job_name, return its latest job_runs row's status + started_at."""
    results: dict[str, dict] = {jn: {"last_status": None, "last_run_at": None} for jn in job_names}

    if not job_names:
        return results

    for jn in job_names:
        q = (
            select(JobRun)
            .where(JobRun.job_name == jn)
            .order_by(JobRun.started_at.desc())
            .limit(1)
        )
        row = (await db.execute(q)).scalar_one_or_none()
        if row:
            results[jn] = {
                "last_status": row.status,
                "last_run_at": row.started_at,
            }

    return results


# ---------------------------------------------------------------------------
# System-status probes (replicate health.py patterns; do NOT import health.py)
# ---------------------------------------------------------------------------


async def probe_postgres(db: AsyncSession) -> bool:
    try:
        await db.execute(select(1))
        return True
    except Exception:
        return False


async def probe_redis() -> bool:
    try:
        import redis.asyncio as redis
        r = redis.from_url(dynamic_settings.get_redis_url())
        await r.ping()
        await r.aclose()
        return True
    except Exception:
        return False


async def probe_compreface() -> bool:
    compreface_url = dynamic_settings.get_compreface_url()
    if not compreface_url:
        return False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{compreface_url}/api/v1/health")
            return resp.status_code == 200
    except Exception:
        return False


async def get_system_status(db: AsyncSession) -> dict:
    """Assemble the system-status response dict."""
    from app.services.scheduler import JOB_REGISTRY

    pg_ok = await probe_postgres(db)
    redis_ok = await probe_redis()
    cf_ok = await probe_compreface()

    if pg_ok and redis_ok:
        overall = "ok"
    elif pg_ok:
        overall = "degraded"
    else:
        overall = "down"

    # Queue depth
    queue_depth = 0
    if pg_ok:
        try:
            result = await db.execute(
                select(func.count(Task.id)).where(Task.status == "pending")
            )
            queue_depth = result.scalar() or 0
        except Exception:
            pass

    # Latest job status for all registered jobs
    job_names = list(JOB_REGISTRY.keys())
    job_statuses = await get_latest_job_status(db, job_names)
    jobs = [
        {
            "job_name": jn,
            "last_status": job_statuses[jn]["last_status"],
            "last_run_at": job_statuses[jn]["last_run_at"],
        }
        for jn in job_names
    ]

    return {
        "overall": overall,
        "services": {
            "postgres": pg_ok,
            "redis": redis_ok,
            "compreface": cf_ok,
        },
        "queue_depth": queue_depth,
        "safe_mode": dynamic_settings.is_safe_mode(),
        "active_event_id": dynamic_settings.get_active_event_id(),
        "jobs": jobs,
    }


# ---------------------------------------------------------------------------
# Config checklist
# ---------------------------------------------------------------------------

# (key, label, required)
_CHECKLIST_DEFS: list[tuple[str, str, bool]] = [
    ("jwt_secret", "JWT Secret", True),
    ("sunday_event_series_id", "Sunday Event Series ID", True),
    ("powerhouse_event_series_id", "Powerhouse Event Series ID", True),
    ("compreface_url", "CompreFace URL", True),
    ("compreface_detect_api_key", "CompreFace Detect API Key", True),
    ("compreface_recognize_api_key", "CompreFace Recognize API Key", True),
    # Recommended
    ("redis_url", "Redis URL", False),
    ("face_retention_days", "Face Retention Days", False),
    ("allowed_domain", "Allowed Domain", False),
    ("google_client_id", "Google Client ID", False),
    ("google_client_secret", "Google Client Secret", False),
    ("sse_heartbeat_seconds", "SSE Heartbeat Seconds", False),
]


async def get_config_checklist(db: AsyncSession) -> dict:
    """Return the config-checklist response dict."""
    result = await db.execute(select(AdminSetting))
    settings_map: dict[str, AdminSetting] = {s.key: s for s in result.scalars().all()}

    items = []
    required_complete = True
    recommended_complete = True
    all_complete = True

    for key, label, required in _CHECKLIST_DEFS:
        setting = settings_map.get(key)
        is_set = False
        if setting is not None:
            val = read_setting_plaintext(setting)
            is_set = val is not None and val != "" and val != "0"

        # Also check dynamic_settings in-memory cache (catches env-seeded values)
        if not is_set:
            cached = dynamic_settings.get(key)
            if cached is not None and str(cached) not in ("", "0"):
                is_set = True

        items.append({"key": key, "label": label, "is_set": is_set, "required": required})

        if not is_set:
            all_complete = False
            if required:
                required_complete = False
            else:
                recommended_complete = False

    return {
        "items": items,
        "required_complete": required_complete,
        "recommended_complete": recommended_complete,
        "all_complete": all_complete,
    }
