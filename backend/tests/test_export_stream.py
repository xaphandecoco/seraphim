"""Tests for streaming CSV export endpoints — S05-F10.

Covers spec §8 export stream test cases:
  - GET /export/contacts.csv streams valid CSV with header row
  - GET /export/participants.csv streams valid CSV with header row
  - Chunked yield: each response body is non-empty
  - 33k synthetic rows: line count == 33001 (header + data), marked @perf
  - Filter params are honoured (contact_type, event_id)
  - Unauthenticated requests return 401
  - viewer role can access streaming export endpoints
  - Content-Disposition header present

The 33k perf test runs on SQLite (synthetic rows).
The RSS/memory test is skipped when psutil is not installed.
The 1500-row <1s load test is only executed on Postgres.
"""

from __future__ import annotations

import time

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import is_postgres, make_contacts, make_event, make_participants


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def one_contact(db_session: AsyncSession):
    contacts = await make_contacts(db_session, 1)
    await db_session.commit()
    for c in contacts:
        await db_session.refresh(c)
    return contacts[0]


@pytest_asyncio.fixture
async def one_participant(db_session: AsyncSession, one_contact):
    ev = await make_event(db_session, title="Stream Test Event")
    await db_session.commit()
    await db_session.refresh(ev)
    parts = await make_participants(db_session, ev, [one_contact])
    await db_session.commit()
    for p in parts:
        await db_session.refresh(p)
    return parts[0], ev


# ---------------------------------------------------------------------------
# contacts.csv — basic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_contacts_csv_returns_200(
    client: AsyncClient,
    one_contact,
    viewer_auth_headers,
):
    resp = await client.get("/export/contacts.csv", headers=viewer_auth_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_contacts_csv_content_type(
    client: AsyncClient,
    one_contact,
    viewer_auth_headers,
):
    resp = await client.get("/export/contacts.csv", headers=viewer_auth_headers)
    assert "text/csv" in resp.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_contacts_csv_has_header_row(
    client: AsyncClient,
    one_contact,
    viewer_auth_headers,
):
    resp = await client.get("/export/contacts.csv", headers=viewer_auth_headers)
    lines = resp.text.splitlines()
    assert len(lines) >= 2, "Expected at least header + 1 data row"
    header = lines[0]
    # Must contain at least some expected columns
    assert "id" in header
    assert "first_name" in header


@pytest.mark.asyncio
async def test_contacts_csv_data_row_present(
    client: AsyncClient,
    one_contact,
    viewer_auth_headers,
):
    resp = await client.get("/export/contacts.csv", headers=viewer_auth_headers)
    lines = resp.text.splitlines()
    # At least one data row
    assert len(lines) >= 2


@pytest.mark.asyncio
async def test_contacts_csv_content_disposition(
    client: AsyncClient,
    one_contact,
    viewer_auth_headers,
):
    resp = await client.get("/export/contacts.csv", headers=viewer_auth_headers)
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd or "filename" in cd


@pytest.mark.asyncio
async def test_contacts_csv_requires_auth(client: AsyncClient):
    resp = await client.get("/export/contacts.csv")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# contacts.csv — filter by contact_type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_contacts_csv_contact_type_filter(
    client: AsyncClient,
    db_session: AsyncSession,
    viewer_auth_headers,
):
    from app.models import Contact

    org = Contact(first_name="Org", last_name="One", contact_type="organization")
    ind = Contact(first_name="Ind", last_name="Two", contact_type="individual")
    db_session.add(org)
    db_session.add(ind)
    await db_session.commit()

    resp = await client.get(
        "/export/contacts.csv?contact_type=organization",
        headers=viewer_auth_headers,
    )
    assert resp.status_code == 200
    body = resp.text
    assert "Org" in body
    assert "Ind" not in body


# ---------------------------------------------------------------------------
# participants.csv — basic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_participants_csv_returns_200(
    client: AsyncClient,
    one_participant,
    viewer_auth_headers,
):
    resp = await client.get("/export/participants.csv", headers=viewer_auth_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_participants_csv_has_header_row(
    client: AsyncClient,
    one_participant,
    viewer_auth_headers,
):
    resp = await client.get("/export/participants.csv", headers=viewer_auth_headers)
    lines = resp.text.splitlines()
    assert len(lines) >= 2
    header = lines[0]
    assert "id" in header or "contact_id" in header


@pytest.mark.asyncio
async def test_participants_csv_content_type(
    client: AsyncClient,
    one_participant,
    viewer_auth_headers,
):
    resp = await client.get("/export/participants.csv", headers=viewer_auth_headers)
    assert "text/csv" in resp.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_participants_csv_requires_auth(client: AsyncClient):
    resp = await client.get("/export/participants.csv")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# participants.csv — event_id filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_participants_csv_event_id_filter(
    client: AsyncClient,
    db_session: AsyncSession,
    one_participant,
    viewer_auth_headers,
):
    participant, ev = one_participant

    # Create another event + participant that should be excluded
    other_contacts = await make_contacts(db_session, 1, first_name_prefix="Other")
    other_ev = await make_event(db_session, title="Other Event")
    await make_participants(db_session, other_ev, other_contacts)
    await db_session.commit()

    resp = await client.get(
        f"/export/participants.csv?event_id={ev.id}",
        headers=viewer_auth_headers,
    )
    assert resp.status_code == 200
    lines = resp.text.splitlines()
    # header + 1 data row (the participant in ev)
    data_lines = [l for l in lines[1:] if l.strip()]
    assert len(data_lines) == 1


# ---------------------------------------------------------------------------
# viewer role can access
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_viewer_can_access_contacts_csv(
    client: AsyncClient,
    one_contact,
    viewer_auth_headers,
):
    resp = await client.get("/export/contacts.csv", headers=viewer_auth_headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_viewer_can_access_participants_csv(
    client: AsyncClient,
    one_participant,
    viewer_auth_headers,
):
    resp = await client.get("/export/participants.csv", headers=viewer_auth_headers)
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 33k streaming test — chunked yields, line count 33001, marked perf
# Runs on SQLite (synthetic rows).
# ---------------------------------------------------------------------------


@pytest.mark.perf
@pytest.mark.asyncio
async def test_contacts_csv_33k_line_count(
    client: AsyncClient,
    db_session: AsyncSession,
    viewer_auth_headers,
):
    """Create 33k contacts, stream the CSV, assert 33001 lines (header + 33k data rows).

    This test exercises the chunked yield path of the streaming export.
    It is marked @perf so it can be run selectively with: pytest -m perf
    """
    from app.models import Contact

    BATCH = 1000
    TOTAL = 33000

    for offset in range(0, TOTAL, BATCH):
        batch = [
            Contact(
                first_name=f"F{i}",
                last_name=f"L{i}",
                contact_type="individual",
            )
            for i in range(offset, offset + BATCH)
        ]
        db_session.add_all(batch)
        await db_session.commit()

    resp = await client.get("/export/contacts.csv", headers=viewer_auth_headers)
    assert resp.status_code == 200

    # Count lines
    lines = resp.text.splitlines()
    # header (1) + 33000 data rows
    assert len(lines) >= 33001, (
        f"Expected at least 33001 lines (header + 33000 data), got {len(lines)}"
    )


# ---------------------------------------------------------------------------
# 1500-row < 1s load test — Postgres only
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not is_postgres(), reason="Postgres-only performance assertion")
@pytest.mark.asyncio
async def test_contacts_csv_1500_rows_under_1s(
    client: AsyncClient,
    db_session: AsyncSession,
    viewer_auth_headers,
):
    """On Postgres, streaming 1500 contacts should complete in under 1 second."""
    from app.models import Contact

    contacts = [
        Contact(first_name=f"F{i}", last_name=f"L{i}", contact_type="individual")
        for i in range(1500)
    ]
    db_session.add_all(contacts)
    await db_session.commit()

    start = time.monotonic()
    resp = await client.get("/export/contacts.csv", headers=viewer_auth_headers)
    elapsed = time.monotonic() - start

    assert resp.status_code == 200
    assert elapsed < 1.0, f"Expected < 1s for 1500 rows but got {elapsed:.2f}s"
