import os

# Configure the test environment BEFORE importing app modules (config/database read
# env vars at import time). A file-based SQLite DB lets the HTTP-path session and the
# pipeline's direct `async_session` usage share ONE database with the same schema.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_seraphim.db")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("STORAGE_PATH", "./.test-storage")
# In-memory rate-limit/Redis storage so the suite needs no Redis server
os.environ.setdefault("REDIS_URL", "memory://")

import asyncio  # noqa: F401  (kept for compatibility with asyncio_mode=auto)
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator

import pytest
import pytest_asyncio
import httpx
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import dynamic_settings
# Use the APP's own engine + session factory so direct `async_session()` usage in the
# recognition pipeline/workers hits the same database the fixtures set up.
from app.database import Base, engine, async_session, get_db
from app.dependencies import check_setup_complete
# Import the FastAPI app eagerly so that ALL ORM models are registered on
# Base.metadata before any fixture runs create_all(). (During S01 Phase 1 this
# was temporarily lazy because router imports were broken; the backend repoint is
# complete now, so eager import is restored — otherwise db_session.create_all
# runs before the models are imported and silently skips tables like `users`.)
from app.main import app  # noqa: F401  (registers all models via router imports)
from app.utils.auth import create_access_token, hash_password

# Stable test secret — must be ≥32 chars (matches our fail-fast assertion)
TEST_JWT_SECRET = "seraphim-test-secret-do-not-use-in-production-x"

# Pre-load the test secret into dynamic_settings so verify_token works in request handlers
dynamic_settings._settings["jwt_secret"] = TEST_JWT_SECRET
dynamic_settings._settings["setup_complete"] = True
dynamic_settings._initialized = True


# ---------------------------------------------------------------------------
# Helper: generate a JWT token for a user dict (no DB call needed)
# ---------------------------------------------------------------------------

def make_token(user_id: int, email: str, role: str, name: str = "") -> str:
    """Create a short-lived access token for test auth headers."""
    return create_access_token(
        {"sub": str(user_id), "email": email, "name": name, "role": role},
        secret=TEST_JWT_SECRET,
        expires_delta=timedelta(minutes=30),
    )


# ---------------------------------------------------------------------------
# Session-level DB cleanup (H1 — deterministic test isolation)
#
# Deletes the SQLite file (+ WAL/SHM siblings) at the START of every test
# session so that run N+1 starts from a pristine database rather than
# the leftover state from run N.  Guarded strictly to file-based sqlite
# URLs so future Postgres URLs (and in-memory :memory: URLs) are no-ops.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def _cleanup_sqlite_db_at_session_start():
    """Remove the SQLite test DB file before the session begins.

    This makes ``pytest tests/ -q`` reproducible across consecutive runs
    without any manual ``rm`` step.  The per-test ``db_session`` fixture
    still creates tables on entry and drops them on exit — this fixture
    only removes the stale file so SQLite opens a fresh empty database.
    """
    import pathlib

    url = os.environ.get("DATABASE_URL", "")
    # Only act on file-based sqlite (not :memory: or postgres)
    if not url.startswith("sqlite") or ":memory:" in url:
        return

    # Strip dialect prefixes: sqlite+aiosqlite:///./path or sqlite:///./path
    # URL format after the scheme is: ///relative or ////absolute
    after_scheme = url.split("///", 1)[-1]
    # Normalize: ./file -> file (pathlib resolves it relative to cwd)
    db_path = pathlib.Path(after_scheme)
    for suffix in ("", "-wal", "-shm"):
        candidate = pathlib.Path(str(db_path) + suffix)
        if candidate.exists():
            try:
                candidate.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# DB engine / session fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Per-test schema for full isolation: create all tables on the app engine, yield a
    session, then drop everything. The HTTP path (overridden get_db) and the pipeline's
    direct `async_session()` both use this same engine, so all writes share one DB."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with async_session() as session:
            yield session
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)


# ---------------------------------------------------------------------------
# Reset the in-memory rate-limiter between tests so counters don't leak across
# tests (otherwise the Nth login in a file trips the 5/min limit).
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
async def _reset_rate_limiter():
    from app.rate_limit import limiter
    storage = getattr(limiter, "_storage", None)
    if storage is not None and hasattr(storage, "reset"):
        try:
            storage.reset()
        except Exception:
            pass
    yield


# ---------------------------------------------------------------------------
# Snapshot/restore global dynamic_settings around every test.
#
# Some endpoints (notably POST /setup) mutate the process-wide
# `dynamic_settings` — e.g. /setup writes a fresh random `jwt_secret` and
# reloads from the DB, which clobbers the TEST_JWT_SECRET this conftest installs.
# Without restoration, every Bearer token in tests that run AFTER a /setup test
# fails verification (401 cascade). Snapshot before, restore after, so no test
# can poison auth/config for the rest of the session.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_dynamic_settings():
    saved_settings = dict(dynamic_settings._settings)
    saved_initialized = dynamic_settings._initialized
    yield
    dynamic_settings._settings = saved_settings
    dynamic_settings._initialized = saved_initialized


# ---------------------------------------------------------------------------
# HTTP client fixture — overrides DB and bypasses setup-complete check
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client(db_session) -> AsyncGenerator[AsyncClient, None]:
    async def override_get_db():
        yield db_session

    async def override_setup_complete():
        """Always treat setup as complete in tests."""
        return None

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[check_setup_complete] = override_setup_complete
    # httpx 0.23+ requires ASGITransport instead of the deprecated `app=` kwarg
    transport = httpx.ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# User fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def admin_user(db_session):
    from app.models import User

    user = User(
        email="admin@lightnc.org",
        password_hash=hash_password("adminpass123"),
        role="admin",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def volunteer_user(db_session):
    from app.models import User

    user = User(
        email="volunteer@lightnc.org",
        password_hash=hash_password("volpass123"),
        role="volunteer",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Token header helpers (return dicts ready to pass as headers=)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def admin_auth_headers(admin_user):
    token = make_token(admin_user.id, admin_user.email, admin_user.role, "Admin")
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def volunteer_auth_headers(volunteer_user):
    token = make_token(
        volunteer_user.id, volunteer_user.email, volunteer_user.role, "Volunteer"
    )
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def viewer_user(db_session):
    from app.models import User

    user = User(
        email="viewer@lightnc.org",
        password_hash=hash_password("viewpass123"),
        role="viewer",
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def viewer_auth_headers(viewer_user):
    token = make_token(
        viewer_user.id, viewer_user.email, viewer_user.role, "Viewer"
    )
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Domain / supporting-data fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def sample_camera(db_session):
    from app.models import Camera

    cam = Camera(
        name="Test Camera",
        rtsp_url="rtsp://localhost/test",
        zone_label="Main Hall",
        fps=1,
        enable_health_check=False,
        status="streaming",
    )
    db_session.add(cam)
    await db_session.commit()
    await db_session.refresh(cam)
    return cam


@pytest_asyncio.fixture
async def sample_event(db_session):
    from app.models import Event

    # Minimal per CN-16 — no event_type, is_active, session_time (those are S04 columns)
    event = Event(
        title="Sunday Service",
        start_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(event)
    await db_session.commit()
    await db_session.refresh(event)
    return event


@pytest_asyncio.fixture
async def sample_contact(db_session):
    from app.models import Contact

    contact = Contact(
        first_name="Juan",
        last_name="dela Cruz",
        email="juan@lightnc.org",
        contact_type="Individual",
    )
    db_session.add(contact)
    await db_session.commit()
    await db_session.refresh(contact)
    return contact


# Backwards-compat alias so tests still using sample_member fixture continue to work
@pytest_asyncio.fixture
async def sample_member(db_session):
    from app.models import Contact

    contact = Contact(
        first_name="Juan",
        last_name="dela Cruz",
        email="juan@lightnc.org",
        contact_type="Individual",
    )
    db_session.add(contact)
    await db_session.commit()
    await db_session.refresh(contact)
    return contact


@pytest_asyncio.fixture
async def sample_detection(db_session, sample_camera, sample_event):
    from app.models import Detection

    detection = Detection(
        camera_id=sample_camera.id,
        image_path="/data/faces/test.jpg",
        confidence=0.95,
        tier="91-99",
        status="tasked",
        matched_name="Juan dela Cruz",
        event_id=sample_event.id,  # use .id (not .event_id) on the new Event model
    )
    db_session.add(detection)
    await db_session.commit()
    await db_session.refresh(detection)
    return detection


@pytest_asyncio.fixture
async def sample_task(db_session, sample_detection):
    from app.models import Task

    task = Task(
        detection_id=sample_detection.id,
        status="pending",
        required_approvals=1,
        current_approvals=0,
        skip_count=0,
        skip_reasons=[],
        expiry_date=datetime.now(timezone.utc).replace(tzinfo=None)
        + timedelta(days=31),
    )
    db_session.add(task)
    await db_session.commit()
    await db_session.refresh(task)
    return task


@pytest_asyncio.fixture
async def sample_participant(db_session, sample_contact, sample_event, sample_detection):
    from app.models import Participant

    record = Participant(
        contact_id=sample_contact.id,
        event_id=sample_event.id,
        detection_id=sample_detection.id,
        status="attended",
        source="face",
    )
    db_session.add(record)
    await db_session.commit()
    await db_session.refresh(record)
    return record


# Backwards-compat alias for tests still referencing sample_attendance
@pytest_asyncio.fixture
async def sample_attendance(db_session, sample_member, sample_event, sample_detection):
    from app.models import Participant

    record = Participant(
        contact_id=sample_member.id,
        event_id=sample_event.id,
        detection_id=sample_detection.id,
        status="attended",
        source="face",
    )
    db_session.add(record)
    await db_session.commit()
    await db_session.refresh(record)
    return record


# ---------------------------------------------------------------------------
# Custom-field fixtures (S02)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def sample_custom_group(db_session):
    from app.models import CustomFieldGroup

    group = CustomFieldGroup(
        name="test_group",
        label="Test Group",
        entity="contact",
        weight=10,
        is_active=True,
    )
    db_session.add(group)
    await db_session.commit()
    await db_session.refresh(group)
    return group


@pytest_asyncio.fixture
async def sample_select_field(db_session, sample_custom_group):
    from app.models import CustomFieldDef

    field = CustomFieldDef(
        group_id=sample_custom_group.id,
        name="pepsol",
        label="PEPSOL Pathway",
        data_type="select",
        options=[
            {"value": "stub_val", "label": "Stub Value"},
            {"value": "other", "label": "Other"},
        ],
        is_required=False,
        is_multi=False,
        weight=10,
        is_active=True,
    )
    db_session.add(field)
    await db_session.commit()
    await db_session.refresh(field)
    return field


@pytest_asyncio.fixture
async def sample_multiselect_field(db_session, sample_custom_group):
    from app.models import CustomFieldDef

    field = CustomFieldDef(
        group_id=sample_custom_group.id,
        name="community",
        label="Community",
        data_type="multiselect",
        options=[
            {"value": "a", "label": "Community A"},
            {"value": "b", "label": "Community B"},
        ],
        is_required=False,
        is_multi=True,
        weight=20,
        is_active=True,
    )
    db_session.add(field)
    await db_session.commit()
    await db_session.refresh(field)
    return field


@pytest_asyncio.fixture
async def sample_contact_ref_field(db_session, sample_custom_group):
    from app.models import CustomFieldDef

    field = CustomFieldDef(
        group_id=sample_custom_group.id,
        name="invited_by",
        label="Invited By",
        data_type="contact_reference",
        options=[],
        is_required=False,
        is_multi=False,
        weight=30,
        is_active=True,
    )
    db_session.add(field)
    await db_session.commit()
    await db_session.refresh(field)
    return field


@pytest_asyncio.fixture
async def sample_checkbox_field(db_session, sample_custom_group):
    from app.models import CustomFieldDef

    field = CustomFieldDef(
        group_id=sample_custom_group.id,
        name="water_baptized",
        label="Water Baptized?",
        data_type="checkbox",
        options=[],
        is_required=False,
        is_multi=False,
        weight=40,
        is_active=True,
    )
    db_session.add(field)
    await db_session.commit()
    await db_session.refresh(field)
    return field
