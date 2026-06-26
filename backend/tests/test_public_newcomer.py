"""Tests for S13 public newcomer intake endpoint (POST /public/newcomer)."""

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Contact, NameMatchReviewQueue, Outbox, Profile


# ---------------------------------------------------------------------------
# Fixture aliases / helpers
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db(db_session: AsyncSession) -> AsyncSession:
    """Alias so tests can use `db` instead of the conftest name `db_session`."""
    return db_session


@pytest_asyncio.fixture
async def public_profile(db_session: AsyncSession) -> Profile:
    """Seed the one is_public profile the newcomer form requires."""
    profile = Profile(
        name="New Friend",
        entity="contact",
        fields=[],
        settings={
            "contact_subtype_default": "New Friend",
            "notify_google_chat": True,
            "notify_gmail": True,
            "success_message": "Thank you! Your information has been recorded.",
        },
        is_public=True,
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    return profile


@pytest_asyncio.fixture
async def seed_contact(db_session: AsyncSession) -> Contact:
    """Seed a contact for name-match resolution tests."""
    contact = Contact(
        first_name="Jose",
        last_name="Reyes",
        contact_type="individual",
        is_deleted=False,
    )
    db_session.add(contact)
    await db_session.commit()
    await db_session.refresh(contact)
    return contact


@pytest.fixture
def mock_anthropic_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force _classify_prayer_request to always return 'valid'."""

    async def _fake_classify(text: str) -> str:
        return "valid"

    monkeypatch.setattr(
        "app.services.profile_service._classify_prayer_request", _fake_classify
    )


@pytest.fixture
def mock_anthropic_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force _classify_prayer_request to always return 'invalid'."""

    async def _fake_classify(text: str) -> str:
        return "invalid"

    monkeypatch.setattr(
        "app.services.profile_service._classify_prayer_request", _fake_classify
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_newcomer_happy_path(
    client: AsyncClient,
    db: AsyncSession,
    public_profile: Profile,
) -> None:
    resp = await client.post(
        "/public/newcomer",
        json={
            "first_name": "Maria",
            "last_name": "Santos",
            "phone": "09171234567",
            "gender": "Female",
            "service_time": "Second Service (10AM)",
            "invited_by": "",
            "prayer_request": "",
        },
    )
    assert resp.status_code == 201, resp.json()
    data = resp.json()
    assert data["status"] == "created"
    assert data["contact_id"] > 0

    result = await db.execute(
        select(Contact).where(Contact.id == data["contact_id"])
    )
    contact = result.scalar_one()
    assert contact.contact_subtype == "New Friend"
    assert contact.phone == "09171234567"


@pytest.mark.asyncio
async def test_honeypot_returns_201_no_row(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """Honeypot-filled submissions are silently accepted but no Contact is created."""
    before = await db.scalar(select(func.count(Contact.id)))
    resp = await client.post(
        "/public/newcomer",
        json={
            "first_name": "Bot",
            "last_name": "Bot",
            "website": "http://spam.example.com",
        },
    )
    assert resp.status_code == 201
    after = await db.scalar(select(func.count(Contact.id)))
    assert after == before


@pytest.mark.asyncio
async def test_rate_limit_5_per_minute(
    client: AsyncClient,
    public_profile: Profile,
) -> None:
    """6th request within the same minute must return 429."""
    for i in range(5):
        r = await client.post(
            "/public/newcomer",
            json={"first_name": f"A{i}", "last_name": "Tester"},
        )
        assert r.status_code == 201, f"Request {i + 1} failed: {r.json()}"

    r6 = await client.post(
        "/public/newcomer",
        json={"first_name": "A", "last_name": "Tester"},
    )
    assert r6.status_code == 429


@pytest.mark.asyncio
async def test_invited_by_matched(
    client: AsyncClient,
    db: AsyncSession,
    public_profile: Profile,
    seed_contact: Contact,
) -> None:
    """SINGLE name-match outcome stores the resolved contact_id in custom_data."""
    resp = await client.post(
        "/public/newcomer",
        json={
            "first_name": "Maria",
            "last_name": "Santos",
            "invited_by": "Jose Reyes",
        },
    )
    assert resp.status_code == 201, resp.json()
    data = resp.json()
    # H1: resolution booleans are NOT returned to anonymous callers (contact-existence oracle)
    assert data["invited_by_resolved"] is None

    # Verify resolution DID happen — check the DB side
    result = await db.execute(
        select(Contact).where(Contact.id == data["contact_id"])
    )
    contact = result.scalar_one()
    assert contact.custom_data.get("invited_by") == seed_contact.id


@pytest.mark.asyncio
async def test_invited_by_unmatched_creates_review_queue(
    client: AsyncClient,
    db: AsyncSession,
    public_profile: Profile,
) -> None:
    """An unmatched invited_by name generates a NameMatchReviewQueue row."""
    resp = await client.post(
        "/public/newcomer",
        json={
            "first_name": "Maria",
            "last_name": "Santos",
            "invited_by": "Xzythqwerty Unknown",
        },
    )
    assert resp.status_code == 201, resp.json()
    # H1: resolution boolean is not echoed to anonymous callers
    assert resp.json()["invited_by_resolved"] is None

    # Verify the review-queue row WAS created (DB side)
    row = await db.scalar(
        select(NameMatchReviewQueue).where(
            NameMatchReviewQueue.raw_name == "Xzythqwerty Unknown"
        )
    )
    assert row is not None
    assert row.source == "newcomer"


@pytest.mark.asyncio
async def test_duplicate_suppression(
    client: AsyncClient,
    db: AsyncSession,
    public_profile: Profile,
) -> None:
    """Second submission with the same name+phone within 5 min returns status=duplicate."""
    payload = {
        "first_name": "Maria",
        "last_name": "Santos",
        "phone": "09171234567",
    }
    r1 = await client.post("/public/newcomer", json=payload)
    assert r1.status_code == 201
    assert r1.json()["status"] == "created"

    r2 = await client.post("/public/newcomer", json=payload)
    assert r2.status_code == 201
    assert r2.json()["status"] == "duplicate"

    # Only one contact row was created
    count = await db.scalar(
        select(func.count(Contact.id)).where(
            Contact.first_name == "Maria",
            Contact.last_name == "Santos",
        )
    )
    assert count == 1


@pytest.mark.asyncio
async def test_outbox_rows_created(
    client: AsyncClient,
    db: AsyncSession,
    public_profile: Profile,
) -> None:
    """Both google_chat and gmail outbox rows are created after a successful submission."""
    await client.post(
        "/public/newcomer",
        json={"first_name": "Ana", "last_name": "Cruz"},
    )
    rows = (
        await db.execute(
            select(Outbox).where(Outbox.status.in_(["pending", "sent", "failed"]))
        )
    ).scalars().all()
    event_types = {r.event_type for r in rows}
    assert "google_chat.new_friend" in event_types
    assert "gmail.new_friend_report" in event_types


@pytest.mark.asyncio
async def test_prayer_request_valid_enqueues_prayer_email(
    client: AsyncClient,
    db: AsyncSession,
    public_profile: Profile,
    mock_anthropic_valid: None,
) -> None:
    """A valid prayer request produces a gmail.prayer_request outbox row."""
    await client.post(
        "/public/newcomer",
        json={
            "first_name": "Ben",
            "last_name": "Lim",
            "prayer_request": "Please pray for my mother's health",
        },
    )
    rows = (
        await db.execute(
            select(Outbox).where(Outbox.event_type == "gmail.prayer_request")
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_prayer_request_invalid_no_prayer_email(
    client: AsyncClient,
    db: AsyncSession,
    public_profile: Profile,
    mock_anthropic_invalid: None,
) -> None:
    """An invalid prayer request must NOT produce a gmail.prayer_request outbox row."""
    await client.post(
        "/public/newcomer",
        json={
            "first_name": "Ben",
            "last_name": "Lim",
            "prayer_request": "N/A",
        },
    )
    rows = (
        await db.execute(
            select(Outbox).where(Outbox.event_type == "gmail.prayer_request")
        )
    ).scalars().all()
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_audit_log_created(
    client: AsyncClient,
    db: AsyncSession,
    public_profile: Profile,
) -> None:
    """contact.create audit_log row is written (by contact_service) for every newcomer."""
    resp = await client.post(
        "/public/newcomer",
        json={"first_name": "Luz", "last_name": "Gomez"},
    )
    assert resp.status_code == 201
    contact_id: int = resp.json()["contact_id"]

    log_row = await db.scalar(
        select(AuditLog).where(
            AuditLog.action == "contact.create",
            AuditLog.entity_id == contact_id,  # entity_id is INTEGER (not str)
        )
    )
    assert log_row is not None


@pytest.mark.asyncio
async def test_newcomer_no_public_profile_404(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """M2: POST /public/newcomer returns 404 and creates no contact when the form is disabled
    (no profile has is_public=True).  Honeypot path still short-circuits to 201 (tested
    separately in test_honeypot_returns_201_no_row which also has no public_profile)."""
    before = await db.scalar(select(func.count(Contact.id)))
    resp = await client.post(
        "/public/newcomer",
        json={"first_name": "Maria", "last_name": "Santos"},
    )
    assert resp.status_code == 404
    after = await db.scalar(select(func.count(Contact.id)))
    assert after == before  # no contact row was created
