# Seraphim CRM — Decisions Log (grilling)

Running log of owner decisions that drive the sprint specs. Updated each grilling round.

## Round 1 — Foundations
- **Migration source:** XLSX exports, but owner will **re-export from CiviCRM with Contact ID added** to the Event/attendance report. This resolves the name-only linkage landmine. → Must confirm email + all custom fields are also included in fresh exports.
- **Custom fields:** **Fully dynamic, CiviCRM-style** custom-field engine (admin can define field groups/fields at runtime). Implies a `custom_field_def` metadata layer + JSONB (or EAV) storage, not just typed columns.
- **Top priority:** **Integrated Facial Recognition.** Reframes the whole project as a *face-recognition-native CRM*. The face↔contact enrollment flow (net-new; no code creates `ComprefaceSubject.contact_id` today) moves to the FRONT of the plan.
- **Cutover:** **Big-bang with a freeze window** (freeze CiviCRM, migrate once, verify, go live).

## Round 2 — Face-recognition core
- **Enrollment model:** (a) **Bulk-ingest historical event photos** → detect/cluster faces → enroll; (b) **auto-enroll from volunteer-confirmed detections** going forward. NOT photo-on-contact-form, NOT kiosk. Enrollment is recognition-pipeline-driven, bootstrapped from a back-catalog of event photos. → Implies a net-new bulk photo-ingestion + face-clustering pipeline.
- **Unknown faces:** Hold in **review/PIT queue** until identified or discarded. Every attendance row maps to a known contact (no anonymous counting).
- **Contact profile face features:** enrolled photos + re-enroll controls; full recognition attendance history. (Recognition-health + relink deferred.)
- **Biometric governance:** Per-contact **consent tracking + member deletion requests (right-to-be-forgotten)**. First-class in the CRM.

## Round 3 — Data model
- **Events/attendance:** One event = one distinct occurrence; **one attendance per person per event**. The 222 "6×" duplicates in the xlsx are an artifact — the report's "Event ID" column is actually the TITLE; those rows are genuinely different events sharing a title. → **Migration MUST use the real numeric Event ID**, not the title. Re-export must include real Event ID.
- **Custom-field engine:** Field **groups + all types** (text/select/multi-select/date/number/checkbox/contact-reference), **no repeating multi-record sets**.
- **People-link fields:** Invited By / Consolidated By / Community Leader / Ministry Leader / Network Leader / Lifegroup Leader become **contact-reference links** (migrate by name match; unmatched → review queue).
- **Multi-value:** **Multi-select supported** (split comma-separated Ministry/Community on migration).

## Round 4 — Workflows & roles
- **Re-export columns confirmed:** real **Contact ID + Event ID** (reliable linkage). Email + extra custom fields (Leader/Church Info) NOT guaranteed → build a **column-mapping importer** that ingests whatever columns are present; email matching for Zoom degrades to name if absent.
- **Cases/Activities:** v1 = **assignable Activities** (assignee, due date, status, notes); multi-step **Cases deferred** to a later phase.
- **Assignee:** **System users only** (admin/volunteer logins).
- **Roles:** admin = configure; volunteer = edit contacts/events/activities; **viewer = reports/dashboard only** (no individual contact records).

## Round 5 — Automation, reporting, UX
- **Automation:** **Full visual rules engine now** (CiviRules-style admin UI: triggers/conditions/actions/delays). Big build; own sprint(s).
- **Integration inputs:** Owner will provide **n8n workflow JSON exports** (all 4). Mattermost/Zoom creds to follow. I cannot open the n8n URLs (auth-gated).
- **Reports:** Owner will **send a screenshot of current reports**; **attendance-trends-over-time** confirmed must-have. Finalize report specs after screenshot.
- **UX:** **Mobile-first, desktop-compatible** (keep current LNC mobile identity, ensure responsive desktop).

## Pending artifacts from owner
1. Fresh CiviCRM exports with **Contact ID + Event ID** (and ideally email + all custom fields).
2. **n8n workflow JSON** exports (×4).
3. **Reports screenshot**.
4. Mattermost webhook URL + Zoom S2S OAuth creds (later).
5. Product name (TBD — using "Seraphim" as working name).

## Round 6 — Enrollment, timeline, ops, scope
- **Photo bootstrap:** Build a **bulk photo upload** that feeds images through the **existing detection → PIT/review queue** (reuse `uploads.py`/`queue_producer`/detection/task/PIT). Volunteer confirms → auto-enroll. New entry point, existing pipeline.
- **Timeline:** **Complete the build before cutover** (comprehensive; CiviCRM stays until feature-complete). No rushed MVP cutover.
- **Hosting:** **Single Unraid host, one app instance.** Cron = in-process **APScheduler**; `job_runs` log. (No multi-replica locking needed.)
- **Gamification:** **Keep** the volunteer leaderboard/points/accuracy layer in the new CRM.

## Round 7 — Reporting (from owner's report suite + exact rules)
- **Report suite** = full attendance analytics (see reports.md): session taxonomy (Morning Prayer/Powerhouse/Community/Events/Sunday 8AM·10AM·3PM, Unique vs Total), summary tiles, Ministry/Zone/PEPSOL rollups, weekly+monthly grids, SMA averages (4/8/12wk, 3/6/9/12mo), MoM deltas vs 12mo avg, zone distribution, **forecasting**.
- **Tier/Active = weeks-since-last-attendance:** Tier0=this week, Tier1=1–4wks, Tier2=5–8wks, Tier3=9–12wks, Inactive=12+wks. Active=Tier0–3.
- **Regular Attendee** = lifetime attendance count **> 9**.
- **Connected** = has Community/CG assignment.
- **Derived attrs maintained by nightly APScheduler job** (snapshot fields on contacts) + on-demand recompute.
- **Community Reports** = leader-submitted attendance (today via n8n forms) → new CRM needs a **Community-Report submission feature**. Attendance has **4 sources**: face-rec, manual, Zoom, community-report forms.
- **Spec impact:** enrich S14 (reporting); add derived-attribute job (S14/S16); add Community-Report forms (extend S13/S18); events need session type + service_time + unique/total counting.

## Round 8 — Name-list attendance, AI matching, notifications
- **Community/Event Reports = attendee NAME LISTS** (not photos/counts). Current n8n flow: Google-Sheet trigger → AI LLM matches each name to a contact (CiviCRM Search → Get Participant ID → Add Attendance) → notify. New CRM must rebuild this as a **name-list attendance intake** + matching service.
- **Name→contact matching:** **deterministic fuzzy first → AI (Claude) fallback for ambiguous → human review queue** for the rest. (Replaces the n8n AI agent. Use Claude per project AI guidance.)
- **Notifications:** **Moving OFF Mattermost → Google Chat, ONE central channel.** Drop per-community routing. S18 integrates Google Chat incoming webhook (not Mattermost).
- **Attendance sources (4):** face-recognition, manual check-in, Zoom (Morning Prayer), name-list reports (AI-matched).

## POST-BUILD INTEGRATION PASS (after spec workflow completes — patch these specs)
1. **S14 Reporting** → full analytics suite per reports.md (session taxonomy + unique/total, summary tiles, Ministry/Zone/PEPSOL rollups, weekly/monthly grids, SMA averages, MoM vs 12mo, forecasting).
2. **Data model** → add contact snapshot fields (last_attended_at, attendance_count, weeks_absent, tier, is_active, is_regular, is_connected) + nightly recompute.
3. **S16** → nightly APScheduler derived-attribute recompute job.
4. **S04/S14** → events need session_type + service_time (8AM/10AM/3PM) + unique-vs-total counting.
5. **New/extended spec** → name-list attendance intake + fuzzy/AI matching service + review queue (likely new S22 or extend S18).
6. **S18** → Google Chat (NOT Mattermost), single channel; Zoom; wire the 4 flows; needs n8n JSON exports.
7. **00-MASTER** + glossary → reconcile all of the above.

## FRAMEWORK STATUS: locked. Spec set generating (15/21); integration pass queued for post-build.

## Round 3 — (pending)

## Open confirmations needed
- Fresh CiviCRM export must include: Contact ID on attendance report; email on contact report; all custom fields (Leader info, Church info, Followup, etc.).
- Handling of the 222 duplicate (person, event) attendance pairs (recurring outreaches).
