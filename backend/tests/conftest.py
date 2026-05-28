import asyncio  # noqa: F401  (kept for compatibility with asyncio_mode=auto)
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.dependencies import check_setup_complete
from app.main import app
from app.utils.auth import create_access_token, hash_password

# Use in-memory SQLite for tests
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

engine = create_async_engine(TEST_DATABASE_URL, echo=False)
AsyncTestSession = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Helper: generate a JWT token for a user dict (no DB call needed)
# ---------------------------------------------------------------------------

def make_token(user_id: int, email: str, role: str, name: str = "") -> str:
    """Create a short-lived access token for test auth headers."""
    from app.config import legacy_settings
    return create_access_token(
        {"sub": str(user_id), "email": email, "name": name, "role": role},
        secret=legacy_settings.SECRET_KEY,
        expires_delta=timedelta(minutes=30),
    )


# ---------------------------------------------------------------------------
# DB engine / session fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="session")
async def db_engine():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    async with AsyncTestSession() as session:
        yield session
        await session.rollback()


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
    async with AsyncClient(app=app, base_url="http://test") as ac:
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
    from app.models import CiviCRMEvent

    event = CiviCRMEvent(
        event_id=1001,
        title="Sunday Service",
        start_date=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db_session.add(event)
    await db_session.commit()
    await db_session.refresh(event)
    return event


@pytest_asyncio.fixture
async def sample_member(db_session):
    from app.models import CiviCRMMember

    member = CiviCRMMember(
        contact_id=5001,
        first_name="Juan",
        last_name="dela Cruz",
        email="juan@lightnc.org",
    )
    db_session.add(member)
    await db_session.commit()
    await db_session.refresh(member)
    return member


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
        event_id=sample_event.event_id,
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
async def sample_attendance(db_session, sample_member, sample_event, sample_detection):
    from app.models import Attendance

    record = Attendance(
        contact_id=sample_member.contact_id,
        event_id=sample_event.event_id,
        detection_id=sample_detection.id,
        status="confirmed",
        push_status="pending",
        push_attempts=0,
    )
    db_session.add(record)
    await db_session.commit()
    await db_session.refresh(record)
    return record


@pytest_asyncio.fixture
async def dead_letter_attendance(db_session, sample_member, sample_event):
    from app.models import Attendance

    record = Attendance(
        contact_id=sample_member.contact_id,
        event_id=sample_event.event_id,
        detection_id=None,
        status="confirmed",
        push_status="dead_letter",
        push_attempts=5,
        last_push_error="CiviCRM connection timeout",
    )
    db_session.add(record)
    await db_session.commit()
    await db_session.refresh(record)
    return record
