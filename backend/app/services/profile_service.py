"""Profile service — S13 form-schema engine and newcomer intake pipeline.

Business logic only; no router imports (CN-25).

Public API:
    get_public_profile_schema(db) -> Optional[PublicProfileSchema]
    render_profile(profile_id, prefill, db) -> ProfileRenderResponse
    validate_profile_fields(fields, db) -> None   (raises 422 on bad field name)
    check_one_public_profile(db, exclude_id) -> None   (raises 409 on conflict)
    process_newcomer_submission(submission, db) -> NewcomerResult

Private helpers:
    _classify_prayer_request(text) -> "valid" | "invalid"
    _enqueue_notification(event_type, payload, db, run_at) -> None
    _drain_outbox_stub(db) -> None

ASSUMPTION (CN-14): S02 should later seed service_time, prayer_request, and season as
  custom_field_def rows so they render in the contact detail view. Until then they are
  stored raw in custom_data, bypassing validate_and_coerce. Tracked as S02 follow-up.

ASSUMPTION (CORS): Cross-origin embedding (different domain) deferred — deployment is
  same-origin (Cloudflare Tunnel -> nginx -> container). Existing app-level
  CORSMiddleware covers the public newcomer form. Revisit if the form is ever embedded
  on a separate church-website domain.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import dynamic_settings
from app.models import Contact, CustomFieldDef, Outbox, Profile, utc_now
from app.schemas import (
    ContactCreate,
    NewcomerResult,
    NewcomerSubmission,
    ProfileFieldDescriptor,
    ProfileRenderResponse,
    ProfileSettings,
    PublicProfileSchema,
)
from app.services import name_match as name_match_svc
from app.services.contact_service import create_contact

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Core field label map for the schema renderer
# ---------------------------------------------------------------------------

_CORE_FIELD_LABELS: dict[str, str] = {
    "first_name": "First Name",
    "last_name": "Last Name",
    "phone": "Phone",
    "email": "Email",
    "gender": "Gender",
    "birth_date": "Birth Date",
    "street_address": "Street Address",
    "contact_subtype": "Contact Sub-type",
    "nickname": "Nickname",
    "suffix": "Suffix",
}


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------


async def get_public_profile_schema(db: AsyncSession) -> Optional[PublicProfileSchema]:
    """Return the resolved schema for the is_public=True profile, or None if none."""
    result = await db.execute(select(Profile).where(Profile.is_public.is_(True)))
    profile = result.scalar_one_or_none()
    if profile is None:
        return None
    rendered = await render_profile(profile.id, {}, db)
    return PublicProfileSchema(
        name=profile.name,
        settings=rendered.settings,
        fields=rendered.fields,
    )


async def render_profile(
    profile_id: int,
    prefill: dict[str, Any],
    db: AsyncSession,
) -> ProfileRenderResponse:
    """Resolve a profile's field list: merge core metadata + custom_field_def info.

    For each field descriptor (sorted by weight):
    - core fields: label defaults from _CORE_FIELD_LABELS, data_type=text
    - custom fields: label/data_type/options from CustomFieldDef row; label_override wins
    Applies prefill values keyed by field.id when provided.
    Raises 404 if profile_id does not exist.
    """
    result = await db.execute(select(Profile).where(Profile.id == profile_id))
    profile = result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile {profile_id} not found",
        )

    settings = ProfileSettings(**(profile.settings or {}))
    raw_fields = profile.fields or []
    sorted_fields = sorted(
        (f for f in raw_fields if isinstance(f, dict)),
        key=lambda f: f.get("weight", 0),
    )

    resolved_fields: list[dict[str, Any]] = []
    for f in sorted_fields:
        field_dict: dict[str, Any] = dict(f)
        field_type = f.get("field_type", "core")
        label_override = f.get("label_override")

        if field_type == "custom" and f.get("custom_field_name"):
            cfd_result = await db.execute(
                select(CustomFieldDef).where(
                    CustomFieldDef.name == f["custom_field_name"]
                )
            )
            cfd = cfd_result.scalar_one_or_none()
            if cfd:
                field_dict["data_type"] = cfd.data_type
                field_dict["options"] = cfd.options
                field_dict["label"] = label_override or cfd.label
            else:
                field_dict["label"] = label_override or f.get("custom_field_name", "")
                field_dict["data_type"] = "text"
                field_dict["options"] = []
        else:
            core_name = f.get("core_field", "")
            field_dict["label"] = (
                label_override
                or _CORE_FIELD_LABELS.get(core_name, core_name.replace("_", " ").title())
            )
            field_dict["data_type"] = "text"
            field_dict["options"] = []

        # Apply prefill value keyed by field.id
        field_id = f.get("id", "")
        if field_id and field_id in prefill:
            field_dict["prefill_value"] = prefill[field_id]

        resolved_fields.append(field_dict)

    return ProfileRenderResponse(
        profile_id=profile_id,
        name=profile.name,
        fields=resolved_fields,
        settings=settings,
    )


async def validate_profile_fields(
    fields: list[ProfileFieldDescriptor],
    db: AsyncSession,
) -> None:
    """Raise 422 if any custom_field_name is not found in custom_field_def (active)."""
    custom_names = [
        f.custom_field_name
        for f in fields
        if f.field_type == "custom" and f.custom_field_name
    ]
    if not custom_names:
        return

    result = await db.execute(
        select(CustomFieldDef.name).where(
            CustomFieldDef.name.in_(custom_names),
            CustomFieldDef.is_active.is_(True),
        )
    )
    found = {row[0] for row in result.all()}
    missing = [n for n in custom_names if n not in found]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown custom field names: {', '.join(missing)}",
        )


async def check_one_public_profile(
    db: AsyncSession,
    exclude_id: Optional[int] = None,
) -> None:
    """Raise 409 if another profile already has is_public=True.

    Pass exclude_id when updating an existing profile so we don't conflict with itself.
    """
    stmt = select(func.count()).select_from(Profile).where(Profile.is_public.is_(True))
    if exclude_id is not None:
        stmt = stmt.where(Profile.id != exclude_id)
    count = (await db.execute(stmt)).scalar_one()
    if count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Another profile is already public. "
                "Set is_public=false on that profile first."
            ),
        )


async def process_newcomer_submission(
    submission: NewcomerSubmission,
    db: AsyncSession,
) -> NewcomerResult:
    """Full newcomer intake pipeline (replaces n8n workflow jdVHzcMWXdANwG8R).

    Steps:
      1. Public profile guard — 404 if no is_public profile (form is disabled)
      2. Duplicate suppression — 5-min window by (first_name, last_name, phone)
      3. Create Contact via create_contact (commits C1; audit_log written automatically)
      4. Name resolution — invited_by + consolidated_by via name_match.match_name
         SINGLE outcome -> contact_id stored in custom_data
         other outcomes -> review queue row created by name_match (auto_enqueue=True)
      5. Merge extra custom fields (service_time, prayer_request, season, first_visit_date)
      6. Prayer request classification (_classify_prayer_request, fail-open)
      7. Enqueue outbox notifications (google_chat + gmail; prayer only if valid)
      8. Commit (C2 — custom_data + outbox rows)
      9. Best-effort drain (_drain_outbox_stub)

    Security notes:
      H1: invited_by_resolved / consolidated_by_resolved are NOT returned in the public
          response — they would act as a contact-existence oracle for anonymous callers.
          The resolution booleans ARE passed into the internal gmail outbox payload.
      M2: gate on is_public profile prevents contact creation when the form is disabled.
      L4: duplicate check uses exact case-insensitive comparison (func.lower) not LIKE
          to prevent wildcard injection ('%' in name matching any recent contact).
    """
    # ------------------------------------------------------------------ #
    # 1. Public profile guard (M2)
    # ------------------------------------------------------------------ #
    profile_result = await db.execute(
        select(Profile).where(Profile.is_public.is_(True))
    )
    public_profile = profile_result.scalar_one_or_none()
    if public_profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No public form is configured",
        )
    settings = ProfileSettings(**(public_profile.settings or {}))
    subtype = settings.contact_subtype_default or "New Friend"
    notify_google_chat = settings.notify_google_chat
    notify_gmail = settings.notify_gmail
    success_message = settings.success_message

    # ------------------------------------------------------------------ #
    # 2. Duplicate suppression (5-minute window)
    # L4: exact case-insensitive match via func.lower — LIKE wildcard bypassed
    # ------------------------------------------------------------------ #
    five_min_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)
    dup_conds: list[Any] = [
        Contact.is_deleted.is_(False),
        func.lower(Contact.first_name) == func.lower(submission.first_name),
        func.lower(Contact.last_name) == func.lower(submission.last_name),
        Contact.created_at >= five_min_ago,
    ]
    if submission.phone:
        dup_conds.append(Contact.phone == submission.phone)

    dup_result = await db.execute(select(Contact).where(*dup_conds).limit(1))
    existing = dup_result.scalar_one_or_none()
    if existing is not None:
        return NewcomerResult(
            contact_id=existing.id,
            status="duplicate",
            message="Your information has already been recorded. Thank you!",
        )

    # ------------------------------------------------------------------ #
    # 3. Build ContactCreate payload
    # Only pass seeded fields through validate_and_coerce to avoid 422.
    # facebook_name is seeded in S02 as type=text.
    # service_time / prayer_request / season are NOT yet seeded (CN-14 pending).
    # invited_by / consolidated_by are resolved below then stored as contact_id ints.
    # ------------------------------------------------------------------ #
    create_custom: dict[str, Any] = {}
    if submission.facebook_name:
        create_custom["facebook_name"] = submission.facebook_name

    contact_payload = ContactCreate(
        first_name=submission.first_name,
        last_name=submission.last_name,
        phone=submission.phone,
        gender=submission.gender,
        birth_date=submission.birth_date,
        street_address=submission.street_address,
        contact_type="individual",
        contact_subtype=subtype,
        custom_data=create_custom,
    )

    # ------------------------------------------------------------------ #
    # 4. Create contact — commits C1 internally; also writes audit_log row
    # ------------------------------------------------------------------ #
    contact, _ = await create_contact(db, contact_payload, actor_id=None)
    contact_id = contact.id  # save id before object may expire

    # ------------------------------------------------------------------ #
    # 5. Name resolution — invited_by and consolidated_by
    # match_name may commit internally (creates NameMatchReviewQueue rows for
    # UNMATCHED/AMBIGUOUS). We do NOT add anything to the session until after
    # name resolution so intermediate commits cannot roll back our state.
    # ------------------------------------------------------------------ #
    extra_custom: dict[str, Any] = {}

    # Map new_friend_add_date -> first_visit_date (seeded S02 key)
    if submission.new_friend_add_date:
        extra_custom["first_visit_date"] = submission.new_friend_add_date
    if submission.service_time:
        extra_custom["service_time"] = submission.service_time
    if submission.prayer_request:
        extra_custom["prayer_request"] = submission.prayer_request
    if submission.season:
        extra_custom["season"] = submission.season

    invited_by_resolved: Optional[bool] = None
    consolidated_by_resolved: Optional[bool] = None

    if submission.invited_by and submission.invited_by.strip():
        try:
            match_result = await name_match_svc.match_name(
                raw_name=submission.invited_by,
                db=db,
                source="newcomer",
                event_id=None,
            )
            if match_result["outcome"] == "SINGLE" and match_result.get("contact_id") is not None:
                extra_custom["invited_by"] = match_result["contact_id"]
                invited_by_resolved = True
            else:
                invited_by_resolved = False
        except Exception:
            log.warning(
                "name_match failed for invited_by=%r", submission.invited_by, exc_info=True
            )
            invited_by_resolved = False

    if submission.consolidated_by and submission.consolidated_by.strip():
        try:
            match_result = await name_match_svc.match_name(
                raw_name=submission.consolidated_by,
                db=db,
                source="newcomer",
                event_id=None,
            )
            if match_result["outcome"] == "SINGLE" and match_result.get("contact_id") is not None:
                extra_custom["consolidated_by"] = match_result["contact_id"]
                consolidated_by_resolved = True
            else:
                consolidated_by_resolved = False
        except Exception:
            log.warning(
                "name_match failed for consolidated_by=%r",
                submission.consolidated_by,
                exc_info=True,
            )
            consolidated_by_resolved = False

    # ------------------------------------------------------------------ #
    # 5b. Reload contact and merge extra custom_data
    # Reload after potential intermediate commits so we don't read stale state.
    # ------------------------------------------------------------------ #
    reload_result = await db.execute(select(Contact).where(Contact.id == contact_id))
    contact = reload_result.scalar_one()

    if extra_custom:
        # Reassign a new dict so SQLAlchemy detects the JSON column change
        contact.custom_data = {**(contact.custom_data or {}), **extra_custom}

    # ------------------------------------------------------------------ #
    # 6. Prayer classification (fail-open: any exception → "invalid")
    # ------------------------------------------------------------------ #
    prayer_valid = False
    if submission.prayer_request and submission.prayer_request.strip():
        try:
            classification = await _classify_prayer_request(submission.prayer_request)
            prayer_valid = classification == "valid"
        except Exception:
            log.warning("Prayer classification raised unexpectedly", exc_info=True)
            prayer_valid = False

    # ------------------------------------------------------------------ #
    # 7. Enqueue outbox notifications
    # ------------------------------------------------------------------ #
    notify_email = dynamic_settings.get_str(
        "newcomer_notify_email", "attendance.monitoring@lightnc.org"
    )
    prayer_email = dynamic_settings.get_str(
        "prayer_notify_email", "attendance.monitoring@lightnc.org"
    )

    if notify_google_chat:
        await _enqueue_notification(
            event_type="google_chat.new_friend",
            payload={
                "webhook_key": "google_chat_webhook_url",
                "text": (
                    f"*New Friend: {contact.first_name} {contact.last_name}*\n"
                    f"Invited by: {submission.invited_by or 'N/A'}\n"
                    f"Consolidated by: {submission.consolidated_by or 'N/A'}\n"
                    f"Service: {submission.service_time or 'N/A'}"
                ),
            },
            db=db,
        )

    if notify_gmail:
        await _enqueue_notification(
            event_type="gmail.new_friend_report",
            payload={
                "to": notify_email,
                "sender_name": "Seraphim CRM",
                "subject": f"New Friend Report: {contact.first_name} {contact.last_name}",
                "template": "new_friend_report",
                "context": {
                    "contact_id": contact_id,
                    "first_name": submission.first_name,
                    "last_name": submission.last_name,
                    "phone": submission.phone,
                    "service_time": submission.service_time,
                    "invited_by": submission.invited_by,
                    "invited_by_resolved": invited_by_resolved,
                    "consolidated_by": submission.consolidated_by,
                    "consolidated_by_resolved": consolidated_by_resolved,
                    "facebook_name": submission.facebook_name,
                },
            },
            db=db,
        )

    if prayer_valid:
        await _enqueue_notification(
            event_type="gmail.prayer_request",
            payload={
                "to": prayer_email,
                "subject": (
                    f"New Friend Prayer Request: {contact.first_name} {contact.last_name}"
                ),
                "template": "prayer_request_report",
                "context": {
                    "prayer_request": submission.prayer_request,
                    "first_name": submission.first_name,
                    "last_name": submission.last_name,
                    "service_time": submission.service_time,
                },
            },
            db=db,
        )

    # ------------------------------------------------------------------ #
    # 8. Commit custom_data updates + outbox rows (C2)
    # ------------------------------------------------------------------ #
    await db.commit()

    # ------------------------------------------------------------------ #
    # 9. Best-effort drain (non-blocking; S17 replaces this with full worker)
    # ------------------------------------------------------------------ #
    try:
        await _drain_outbox_stub(db)
    except Exception:
        log.warning("_drain_outbox_stub raised unexpectedly", exc_info=True)

    # H1: Do NOT echo resolution booleans to the anonymous public caller.
    # Returning invited_by_resolved=True/False would let an attacker confirm
    # whether a named individual is in the member database (religious-affiliation PII).
    # The booleans ARE preserved in the internal gmail outbox payload (above).
    return NewcomerResult(
        contact_id=contact_id,
        status="created",
        message=success_message,
        invited_by_resolved=None,
        consolidated_by_resolved=None,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _classify_prayer_request(text: str) -> Literal["valid", "invalid"]:
    """Classify prayer-request text as 'valid' or 'invalid' via Claude.

    Empty/blank text -> 'invalid' without any API call.
    Fail-open: on any exception (missing key, API error, parse error) -> 'invalid'.
    Model is configurable via admin_settings key 'prayer_classifier.claude_model'.
    SDK reads ANTHROPIC_API_KEY from environment by default.
    Structured to allow monkeypatching at this function boundary in tests.
    """
    if not text or not text.strip():
        return "invalid"
    try:
        import anthropic  # lazy import; fails gracefully below

        model = dynamic_settings.get_str(
            "prayer_classifier.claude_model", "claude-sonnet-4-6"
        )
        client = anthropic.AsyncAnthropic()
        response = await client.messages.create(
            model=model,
            max_tokens=10,
            system=(
                "You are a church prayer team assistant. "
                "Classify the following as 'valid' (a genuine prayer request like health, "
                "family, guidance) or 'invalid' (not a prayer request: N/A, none, "
                "good luck, etc.). Reply with exactly one word: valid or invalid."
            ),
            messages=[{"role": "user", "content": text}],
        )
        word = response.content[0].text.strip().lower()
        if word == "valid":
            return "valid"
        return "invalid"
    except Exception:
        log.warning(
            "Prayer classification failed; defaulting to invalid", exc_info=True
        )
        return "invalid"


async def _enqueue_notification(
    event_type: str,
    payload: dict[str, Any],
    db: AsyncSession,
    run_at: Optional[datetime] = None,
) -> None:
    """Insert an Outbox row with status='pending' for later delivery by S17 worker."""
    row = Outbox(
        event_type=event_type,
        payload=payload,
        status="pending",
        attempts=0,
        run_at=run_at,
        created_at=utc_now(),
    )
    db.add(row)


async def _drain_outbox_stub(db: AsyncSession) -> None:
    """Best-effort synchronous drain of pending outbox rows for this request.

    google_chat.*  — POST to configured webhook URL (5s timeout); mark sent/failed.
                     If webhook URL is not configured: skip (row stays pending).
    gmail.*        — Log stub + mark sent to avoid retry accumulation before S18.

    S17 replaces this with a proper at-least-once async background worker.
    """
    import httpx

    result = await db.execute(
        select(Outbox)
        .where(Outbox.status == "pending", Outbox.attempts < 3)
        .order_by(Outbox.created_at)
        .limit(10)
    )
    rows = result.scalars().all()
    if not rows:
        return

    async with httpx.AsyncClient(timeout=5.0) as client:
        for row in rows:
            try:
                if row.event_type.startswith("google_chat."):
                    webhook_url = dynamic_settings.get_str("google_chat_webhook_url", "")
                    if not webhook_url:
                        # Guard missing webhook — skip; S17 worker retries
                        continue
                    r = await client.post(
                        webhook_url,
                        json={"text": (row.payload or {}).get("text", "")},
                    )
                    r.raise_for_status()
                    row.status = "sent"
                    row.attempts += 1
                elif row.event_type.startswith("gmail."):
                    # Pre-S18 stub: log and mark sent to avoid endless retry
                    log.info(
                        "gmail stub: would send event_type=%s to=%s",
                        row.event_type,
                        (row.payload or {}).get("to", ""),
                    )
                    row.status = "sent"
                    row.attempts += 1
            except Exception as exc:
                row.status = "failed"
                row.attempts += 1
                # L2: do NOT store str(exc) — httpx errors stringify the full webhook
                # URL including its secret token. Store only a redacted summary.
                http_status = getattr(getattr(exc, "response", None), "status_code", "n/a")
                row.last_error = f"{type(exc).__name__}: status={http_status}: event={row.event_type}"

    await db.commit()
