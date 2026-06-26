feat(S13): profiles & public newcomer form

## Summary

Complete intake-form templates (profiles) with public-facing unauthenticated newcomer form. Admin profile builder (CRUD + render preview). Backend intake pipeline: 5-min deduplication, contact creation via S03, name resolution via S22 (invited_by/consolidated_by), Claude prayer-classification (fail-open), transactional outbox producer stub. Rate-limited public endpoints. Security: HIGH membership-oracle authz leak + 3 MEDIUM + 3 LOW findings fixed pre-commit.

- **`profiles` table & ORM model:** name, description, is_public, fields (JSONB form schema)
- **Admin CRUD endpoints:** list/detail/create/update (duplicate-name 409, custom-field-name 422) / delete (last-public 409 guard)
- **Public endpoints:** `GET /public/newcomer/profile` (returns is_public profile or 404) + `POST /public/newcomer` (NewcomerSubmission; rate-limited 30/min per IP)
- **Newcomer intake pipeline:** 5-min dedupe (exact email+name match), S03 contact creation + audit, S22 name resolution, Claude prayer-classification (claude-sonnet-4-6), transactional outbox enqueue [google_chat.new_friend + gmail.new_friend_report always; gmail.prayer_request only if valid]
- **Frontend UI:** WelcomePage (public, no auth), ProfilesPage (admin list + inline toggle), ProfileFormPage (admin builder with live preview), ContactsPage "New from template" dropdown
- **Security:** PASS (HIGH membership oracle + 3 MEDIUM + 3 LOW fixed pre-commit; 4 regression tests added)
- **⚠️ M3 ops gate (pre-public-launch):** rate-limiter requires nginx proxy-headers + X-Forwarded-For, else per-IP limiting breaks; must document + validate pre-cutover

## Definition of Done

- [x] Migration `s13a1b2c3d4e5` (down_revision `s12a1b2c3d4e5`): new `profiles` table; seeds 3 preset profiles (New Friend [is_public=true], Community Member, Volunteer) with `fields=[]`
- [x] `Profile` SQLAlchemy ORM model with proper indexes + constraints
- [x] `ProfileService` (newcomer intake pipeline: 5-min dedupe, S03 contact creation, S22 name resolution, Claude prayer-classification, transactional outbox enqueue + drain_stub)
- [x] Admin CRUD endpoints in `routers/profiles.py`: list/detail/create/update/delete with duplicate-name 409, custom-field-name 422, last-public 409 guards
- [x] Public endpoints in `routers/public.py`: GET /public/newcomer/profile (404 if no is_public) + POST /public/newcomer (rate-limited 30/min); public response unconditionally returns invited_by_resolved=None/consolidated_by_resolved=None (no PII leak)
- [x] Frontend ProfilesPage (admin list + inline is_public toggle + delete), ProfileFormPage (admin builder with ProfileFormRenderer + SectionRenderer + ProfileFieldEditor + live preview), WelcomePage (public form, no auth, no BottomNav, conditional prayer-request field), ContactsPage "New from template" dropdown
- [x] Hooks/services/types for TanStack Query integration
- [x] Security audit PASS (HIGH membership oracle + 3 MEDIUM + 3 LOW fixed pre-commit; 4 regression tests)
- [x] test_migrations.py EXPECTED_HEAD updated to `s13a1b2c3d4e5`

## Test Plan

- [x] Backend 19 isolated S13 tests: **passed/0 failed** (incl. test_newcomer_no_public_profile_404, dedupe guards, name-resolution fallback, prayer-classification fail-open)
- [x] Regression slice (contacts/custom_fields/name_match): **89 passed/0 failed**
- [x] Final consolidated: **31 passed/0 failed**
- [x] App imports clean (no circular deps; profiles module properly exported)
- [x] Frontend build + lint: **clean**
- [x] Frontend `npm run test:run`: **501 tests passed** (incl. WelcomePage, ProfilesPage, ProfileFormPage, ProfileFormRenderer, hooks)
- [x] Security audit: **PASS** (HIGH membership oracle + 3 MEDIUM + 3 LOW fixed pre-commit; 4 regression tests added)

## Notable Implementation Details

1. **Security fix (HIGH → PASS):** Public POST `/public/newcomer` response was leaking `invited_by_resolved.id`, `consolidated_by_resolved.id` (contact existence oracle for religious-affiliation categories). Fixed: response unconditionally returns `invited_by_resolved=None, consolidated_by_resolved=None`; name resolution kept only in internal outbox context.

2. **Security hardening (MEDIUM → PASS):** (a) NewcomerSubmission string fields now have max_length bounds (first_name/last_name VARCHAR(100), email VARCHAR(255), phone VARCHAR(20), prayer_request VARCHAR(2000)). (b) POST /public/newcomer returns 404 "No public form is configured" when no is_public profile exists (form has a real off-switch, not silent drop). (c) Rate-limit on GET/POST /public/newcomer keys on per-IP client address; documented M3 ops requirement: uvicorn MUST run with `--proxy-headers --forwarded-allow-ips` + nginx must set trusted X-Forwarded-For, else limiting collapses to global or becomes XFF-spoofable.

3. **Outbox producer stub:** Transactional outbox enqueues google_chat.new_friend + gmail.new_friend_report (always) + gmail.prayer_request (only if prayer-classification succeeded + is_valid). Full NotificationService + push/email dispatch awaits S17/S18 expansion.

4. **Newcomer pipeline:** 5-min duplicate suppression (exact email + first_name+last_name match), S03 contact creation (auto-audits), S22 name resolution for invited_by/consolidated_by (returns NameMatchResult; outcome SINGLE/AMBIGUOUS/UNMATCHED), Claude prayer-classification (model claude-sonnet-4-6, fail-open, skipped in tests via ENVIRONMENT==test check).

5. **Unseeded custom fields:** service_time, prayer_request, season are NOT seeded by S02 yet. Decision: merge directly into contact.custom_data post-creation (bypassing validate_and_coerce). CN-14 follow-up: S02 should seed these fields so they render in contact detail.

6. **Outbox reuse:** Outbox model created in S12; S13 imports and populates it. No new model needed.

## Carry-Forwards

**🟡 Documented in docs/BLOCKERS.md — Owner decisions pending:**

- **[S13] M3 rate-limit-proxy requirement (pre-public-launch ops gate):** GET/POST /public/newcomer rate-limited (30/min) by per-IP client address (get_remote_address). Behind nginx + Cloudflare Tunnel, uvicorn MUST run with `--proxy-headers --forwarded-allow-ips <nginx>` and nginx must set trusted X-Forwarded-For, else per-IP limiting collapses to one global bucket or becomes XFF-spoofable. **MUST document in PRODUCTION_RUNBOOK.md + validate pre-cutover.**

- **[S13] CN-14 follow-up:** S02 should seed custom_field_def rows for service_time, prayer_request, season so they render in contact detail after newcomer submission.

- **[S13] ContactFormPage `?profile=` param (deferred):** ProfilesPage "New from template" dropdown currently creates a contact; deeper template pre-fill integration is post-S13 enhancement.

- **[S13] CORS (same-origin):** dynamic_settings has no get_json method; same-origin deployment (Cloudflare Tunnel → nginx → same container) sufficient; per-request CORS deferred.

- **[S13] Outbox expansion (S16/S17):** Stub persists rows; full NotificationService + push/email awaits S17/S18.

## Rollback Notes

Rollback is safe (Alembic downgrade):
```bash
cd backend && alembic downgrade s12a1b2c3d4e5
```

Migration is pure-additive (new `profiles` table); downgrade drops it. No data loss in contacts or other core tables.

## Migration Head

**New Alembic head:** `s13a1b2c3d4e5`  
**Down revision:** `s12a1b2c3d4e5`

Applied via:
```bash
cd backend && alembic upgrade head
```

---

Generated by senpai-v2-docs  
Green gate: backend ✅ + frontend ✅ + security ✅  
Date: 2026-06-26
