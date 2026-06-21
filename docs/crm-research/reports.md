# Seraphim CRM — Reporting Requirements (from owner's current report suite)

Source: owner's Google-Sheet attendance dashboard (fed today from CiviCRM via the "Event Reports → CiviCRM" n8n flow). Sheet: https://docs.google.com/spreadsheets/d/1dHt5_l2OTuQto8zFj4DANszHlDLd-qvkvHkqiD3harY/edit
The new CRM must replace BOTH the CiviCRM data layer AND this Google-Sheet reporting layer. This is the authoritative scope for **S14 — Reporting Dashboard** and adds **derived member attributes** to the data model.

## A. Session / service taxonomy (event categories)
Attendance is grouped by these session types, each counted two ways — **Unique** (distinct people) and **Total** (all check-ins):
- Morning Prayer (Unique / Total)
- Powerhouse (Unique / Total)
- Community (Database Unique / Total Non-Unique)
- Events (Total)
- Sunday Service — All (Unique / Total) and split by service time **8AM / 10AM / 3PM**
- New Contacts (count of first-timers in the period)
- Rollup lines: **Total Unique**, **Total Database**, **Total w/ New Contacts & Community Reports**
→ Implication: `events.event_type` must cover these session categories (or a `session`/`service_time` attribute). Reporting needs unique-vs-total dual counting.

## B. Derived member classifications (COMPUTED — definitions CONFIRMED by owner)
Computed from attendance recency/count + community assignment. Maintained by a **nightly APScheduler job** that stamps snapshot fields on each contact (so reports + lists are fast); also recomputable on demand.

- **Tier / Active status = weeks since last attendance** (single system):
  - **Tier 0** — not absent this week (attended this week)
  - **Tier 1** — 1–4 weeks absent
  - **Tier 2** — 5–8 weeks absent
  - **Tier 3** — 9–12 weeks absent
  - **Inactive** — 12+ weeks absent
  - **Active = Tier 0–3** (attended within 12 weeks); **Inactive = 12+ weeks**.
  - Compute: `weeks_absent = floor((today - last_attended_date) / 7)`; bucket per above.
- **Regular Attendee vs Non-Regular** — Regular = **more than 9 attendances** (lifetime attendance count > 9). Else Non-Regular.
- **Connected vs Not Connected** — = assigned to a Community/CG (has a community + community_leader). Reported within Regular Attendees and per Tier.
- **New Contact** — first appears (created/first attendance) within the period.
- **Total Leaders with CGs** — count of contacts who lead a community group (44).

## C. Summary dashboard tiles
1. **Contact Information**: All Contacts; Regular vs Non-Regular (count + %); Connected vs Not-Connected Regular (count + %).
2. **Status**: Active vs Inactive contacts; Active vs Inactive Regular Attendees.
3. **Tier Level Summary**: Tier 1/2/3/Inactive × (All Members | Connected | Not Connected).
4. **Total Leaders with CGs**.
5. **Ministry Information**: volunteers per ministry (Service Pastors, Ushering, IT-Multimedia/Monitoring/Reports, Music, Dance, Parking, Prayer Gatherings, Welcome Center, Admin Office, Stewardship, Housekeeping, Property Custodian, Arise & Build, Grievance, Learning & Dev, Community Heads, Outreach Facilitators, Missions, Visitations) + Total.
6. **Community and Zone Information**: members per zone (Kids, Youth, Young Adults, Young Professionals, Young Couples, Adult, Adult Men, Adult Women) + Total.
7. **PEPSOL Information**: Encounter Graduate, Prepare to Serve, SOL 1/2/3, Graduate (discipleship pathway counts).
8. **Reports Coordinators / org structure** (informational contact block — low priority).

## D. Attendance time-series
- **Weekly Attendance** grid: rows = section A metrics; columns = recent weeks (Week Start/End). Conditional color (high/low).
- **Monthly Attendance** grid: same metrics by month.
- Each grid shows "Last Database Update" date.

## E. Analytics
- **Averages (SMA)**: per metric — 4-week, 8-week, 12-week, 3-month, 6-month, 9-month, 12-month moving averages.
- **Monthly Report**: Previous month vs Current month, Difference, Difference vs 12-month average (%).
- **Monthly Zone Attendance**: per zone (Kids/Youth/Young Adults/Adult Men/Adult Women/No Community) Previous vs Current, Difference, Distribution %.
- **Monthly Reports Attendance**: per report category (Kids/Youth/Young Adults/Adult Men/Adult Women/Outreach/Ministry/Administrative) — "Community Reports" submitted by leaders.
- **Monthly Forecasting**: projected this-month vs actual (Difference, Difference %) and **Next-month forecast** per metric.

## F. Implications for the build
- `events`/sessions need: type + service time (8AM/10AM/3PM) + a unique/total counting model.
- New derived attributes on contact (Regular, Active, Connected, Tier, is_leader_with_cg) — compute via nightly APScheduler job (S16) writing snapshot fields, OR live views. Reports read these.
- Need a **Community Reports** concept: leaders submit per-zone/ministry attendance counts (the "Reports Attendance" + "Total w/ New Contacts & Community Reports" lines). This is a separate input stream beyond face-recognition attendance — CONFIRM with owner.
- "Powerhouse", "Morning Prayer", "Community" are recurring session types — some are Zoom (morning prayer → the Zoom n8n flow).
- Forecasting = simple trend/seasonal projection; SMA already defined.

## G. CONFIRMED scope additions (from owner answers)
- **Derived attributes** (Tier/Active/Regular/Connected) computed by a **nightly job** stamping snapshot fields on contacts; recomputable on demand. Definitions in §B.
- **Community / Event Reports = attendee NAME LISTS (not photos, not just counts).** Leaders/coordinators enter the list of who attended (today into a Google Sheet). The current n8n "Event Reports → CiviCRM" flow is **Sheet-triggered**, then an **AI LLM agent** matches each name to a CiviCRM contact (CiviCRM Search → Get Participant ID → Add Attendance), handling nicknames/spelling variants, and **posts a notification to the matching Mattermost community channel** (a large switch routes per community/zone). → New CRM must provide: (a) an **attendance-by-name-list intake** (form/UI replacing the Google Sheet), (b) an **AI/fuzzy name→contact matching service** with a review queue for ambiguous/unmatched names, (c) **per-community Mattermost routing** of notifications. Fold into S13 (forms) + S17/S18 (automation + integrations).
- **Attendance sources (4):** (1) face-recognition on-site (CompreFace match), (2) manual check-in, (3) Zoom (Morning Prayer / online, by email/name), (4) **name-list reports matched by AI LLM** (community/event reports). NOTE: the *face* bulk-photo upload (S07) is for ENROLLING faces only — it is NOT how event attendance reports work.
