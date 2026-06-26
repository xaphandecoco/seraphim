# n8n Automation — Ground Truth (pulled via API 2026-06-21)

Full workflow JSON saved in `docs/crm-research/n8n/<id>.json`; raw prompt/endpoint dump in `n8n/_prompts_and_calls.txt`. 30 workflows total; the CRM-relevant ones below. Everything talks to CiviCRM via `POST/GET https://crm.lightnc.org/wp-content/plugins/civicrm/civicrm/extern/rest.php` (APIv3). LLMs are OpenRouter/OpenAI today → use **Claude** in the rebuild. Data entry is **Google Forms → Google Sheets**; notifications go to **Mattermost + Gmail** today → **Google Chat (single channel) + Gmail** in the rebuild.

## IN-SCOPE workflows (the CRM must absorb these)

### 1. New Friend V2 (`jdVHzcMWXdANwG8R`, 28 nodes) — newcomer intake
- Trigger: Google Sheets (a "New Friend" Google Form feeds a sheet).
- AI agents resolve **Invited By** and **Consolidated By** free-text names → CiviCRM contact IDs (Filipino-name aware, "first 3 letters" heuristic; **if unsure → contact ID '1'** = unmatched bucket). `textClassifier` routes. Creates/updates the contact, then notifies Mattermost + Gmail.
- **Rebuild → S13** (public newcomer form) + **S22** (name matching) + **S18** (Google Chat/Gmail notify).

### 2. Gforms AI V2 (`3yaS8JZfct8BVe8Z`, 138 nodes) — community/event reports (the complex one)
- Trigger: Google Sheets + "On form submission" → **Switch** by submission type.
- **Event Form fields**: Event Title, Date of Activity, Time of Activity, Location, Number of Attendees (incl. Leader), **Event Leader**, **Attendees** (name list), Topics Discussed, Prayer Items, Remarks/Comments/Praise Reports, **Photo Documentation**. → a full **cell-group/community meeting report** (attendance + qualitative + photos).
- "Comparison" AI agent matches the attendee name list to members (CiviCRM Search), then "AI Agent1" adds attendance per matched contact + posts to the community's Mattermost channel.
- ALSO handles **Finance Liquidation Reports** + **Budget Request Forms (BRF)** with Google Drive folder org + Gotenberg HTML→PDF. → **OUT OF CRM SCOPE** (separate finance module; note + exclude).
- **Rebuild → S22** (attendance-by-name-list intake + AI matching + review queue) + a **Community Report** entity (event + attendees + topics/prayer/remarks + photos) + **S18** notify. Finance excluded.

### 3. CiviCRM Scheduler/Updater (`fKkPUolayRyZrjao`, 92 nodes) — the cron brain
Schedule triggers (cron):
- **Sunday SVC** `0 18 * * 5` (Fri 6PM) — generate the Sunday service event(s).
- **Powerhouse Generator** `0 8 * * 3` (Wed 8AM) — generate Powerhouse event.
- **Rescan EOW** `0 0 * * 1` (Mon 00:00) — end-of-week recompute (attendance + derived attrs).
- **Rescan EOM** (daily noon) — end-of-month recompute.
- **Attendance Notifiers**: 8AM `30 9 * * 0`, 10AM `0 12 * * 0`, 3PM `0 17 * * 0` (Sun), Powerhouse `0 21 * * 3` (Wed) — post per-service attendance summaries.
- **AI Agent1**: re-checks previously-unmatched names against a learned **"common names" alias list** + CiviCRM → recovers matches.
- **Rebuild →** event generation **S04**; scheduled jobs **S16**; derived-attr recompute **S23**; notifiers **S18**; alias-dictionary re-match **S22**.

### 4. Morning Prayer (`sJ7EgCZkk9wKSj3D`, 82 nodes) — Zoom attendance
- Trigger: Schedule (≈23:00). Generates the prayer event, pulls Zoom attendees, "Comparison Agent" matches names (batches of 20, drop to 10 if ambiguous; exact-match batch lookup), adds attendance to CiviCRM, notifies.
- **Rebuild → S18** (Zoom S2S OAuth pull) + **S22** (matching) + **S04** (recurring prayer event).

### 5. Create Schedule (`pz7sHlUbU6jV1Hqm`, 7 nodes) — event creation via webhook
- Webhook → AI agent → Google Calendar + Google Sheets + Gmail. Creates event schedules.
- **Rebuild → S04** (event CRUD/series) + **S19** (API/webhook).

## OUT OF SCOPE (note, do not build into CRM v1)
- Finance: Liquidation/BRF (inside Gforms AI V2), `EOM Comparison Report`, `Weekend Reports`.
- IT/Network: `Multimedia Handoff`, `MikroTik Setup`, `MIkrotik Registration`, `Speedtest Notifier`, `IT Notification`.
- Misc/assistants: `SOLA` (multi-tool AI assistant — School of Leaders App?), `Portfolio Form`, `Photography`, older `New Friend (old)`, `GForms AI (old)`, `Gforms AI`.

## Key design takeaways for the rebuild
1. **Name matching is the backbone of attendance.** Build it once (S22): normalize → deterministic fuzzy (Filipino-aware, nickname/first-3-letters) → **name-alias dictionary** (the "common names" list, learned over time) → **Claude fallback** → **review queue** (the "ID 1 if unsure" behavior). Reused by New Friend, community reports, Zoom.
2. **Community Report ≠ just counts.** It's event + attendee-name-list + Topics/Prayer/Remarks + photos, submitted by a leader. Model it (S22) and feed both attendance and the "Reports Attendance" report lines.
3. **Scheduled event generation + recompute + notifiers** are first-class (S04/S16/S23/S18), replacing the cron workflow. Cron expressions above are the exact cadences to replicate.
4. **Photos in reports are documentation**, not face-enrollment (that's S07's separate bulk upload).
5. Notifications consolidate to **Google Chat (one channel) + Gmail**.
