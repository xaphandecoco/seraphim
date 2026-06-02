# Project Seraphim — User Guide

**Light of the World Worldwide Ministries — North Caloocan (LNC)**

A step-by-step guide for volunteers and administrators using the LNC Attendance System.

---

## Table of Contents

- [Getting Started](#getting-started)
- [Volunteer Guide](#volunteer-guide)
  - [Logging In](#logging-in)
  - [Reviewing Tasks](#reviewing-tasks)
  - [Quality Audit](#quality-audit)
  - [Understanding Confidence Tiers](#understanding-confidence-tiers)
  - [Leaderboard](#leaderboard)
  - [Events & Members](#events--members)
- [Admin Guide](#admin-guide)
  - [Setup Wizard](#setup-wizard)
  - [Setting the Active Event](#setting-the-active-event-important)
  - [Managing Cameras](#managing-cameras)
  - [Managing Users](#managing-users)
  - [PIT Queue](#pit-queue)
  - [System Settings](#system-settings)
  - [Recognition & Queue Tunables](#recognition--queue-tunables)
  - [Attendance Push](#attendance-push)
  - [Analytics Dashboard](#analytics-dashboard)
  - [Logs](#logs)
- [Appearance](#appearance)
- [Installing as an App (PWA)](#installing-as-an-app-pwa)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)

---

## Getting Started

### What is Project Seraphim?

Project Seraphim is a facial recognition attendance system for LNC. It uses cameras at church entrances to detect faces, matches them against enrolled members, and logs attendance to CiviCRM. Volunteers review uncertain matches to ensure accuracy.

### Who uses it?

| Role | What they do |
|------|-------------|
| **Volunteer** | Review face recognition tasks, confirm/edit/skip matches, run quality audits, view leaderboard |
| **Admin** | Everything a volunteer does, plus: manage cameras, users, settings, push attendance to CiviCRM, resolve PIT queue, view analytics |

### First Login

1. Open the app in your browser (ask your admin for the URL)
2. Enter your email and password
3. If your admin enabled Google OAuth, the "Sign in with Google" button will appear automatically

---

## Volunteer Guide

### Logging In

- Use the email and password provided by your admin
- If you forget your password, ask an admin to generate a reset link for you
- The system keeps you logged in for 7 days via a secure cookie

### Reviewing Tasks

The **Tasks** page is your main workspace. Face detection cards appear here for review.

#### Task Card Layout

Each card shows:
- **Face thumbnail** — the detected face from the camera
- **Matched name** — who the system thinks this is (or "Unknown")
- **Confidence %** — how sure the AI is (e.g., 97.5%)
- **Tier badge** — how many approvals are needed:
  - **Auto** (≥98%) — already confirmed automatically; you won't see these
  - **1-Vol** (91–97.9%) — needs 1 volunteer approval
  - **2-Vol** (<91%) — needs 2 volunteer approvals
  - **Unknown** — no match found; needs 2 approvals
- **Camera name** — which camera detected this face
- **Time** — when the detection happened

#### Actions

| Button | When to use | What happens |
|--------|-------------|--------------|
| **Confirm** (gold) | The matched name is correct | Approval recorded. When enough approvals are reached, attendance is logged. |
| **Edit** | The system matched the wrong person | Search for the correct member, select them. |
| **Add** | This person is not yet linked to a member | Search CiviCRM members, select the correct one. |
| **Skip** | You can't identify this face | Choose a reason. The task returns to the queue for another volunteer. |

> **Cooldown:** After each action there is a 3-second cooldown before you can act on another task. Error toasts will tell you exactly what went wrong (cooldown, double-action, etc.).

> **Dual-approval note:** Each volunteer can only act on a given task once. Two different volunteers must approve low-confidence tasks.

#### Queue Status Banners

Banners appear at the top of the Tasks page:
- **Safe Mode** — recognition is paused by an admin; no new tasks will arrive
- **Queue Saturated** — too many pending tasks; camera ingestion is paused temporarily

### Quality Audit

The **Audit** tab (shield icon) shows quality-check cards for previously enrolled faces. These appear automatically when the main task queue is empty.

| Button | Meaning |
|--------|---------|
| **Correct** | The enrolled match is accurate |
| **Wrong** | The match is incorrect; flags it for review |
| **Change** | Search for the correct member and reassign |

Incorrect confirmations found during audit reduce the original volunteer's accuracy score.

### Understanding Confidence Tiers

| Tier | Score | Approvals Needed | Your action |
|------|-------|-----------------|-------------|
| Auto | ≥98% | 0 (automatic) | None — already logged |
| 1-Vol | 91–97.9% | 1 volunteer | Confirm if correct |
| 2-Vol | <91% | 2 volunteers | Confirm if correct |
| Unknown | No match | 2 volunteers | Use Edit or Add to identify |

### Leaderboard

The **Ranking** page shows volunteer standings. Switch between This Month, Previous Month, and All Time using the period selector.

| Action | Points |
|--------|--------|
| Confirm or Add | +1 |
| Edit (name change) | +2 |
| Incorrect confirmation (found by audit) | −3 |

### Events & Members

- **Events** — upcoming church events synced from CiviCRM; admins can tap **Sync** to pull the latest
- **Members** (Attendees) — enrolled members with face thumbnails and sample counts; admins can **Sync** to update from CiviCRM

---

## Admin Guide

### Setup Wizard

The first time the system starts, complete the 5-step setup wizard:

1. **Database** — PostgreSQL and Redis connection details; use "Test Connections" to verify before advancing
2. **External Services** — Compreface URL/API key and CiviCRM credentials (optional at this stage)
3. **Admin Account** — create the first admin user (password must be ≥12 characters with uppercase, lowercase, number, and special character)
4. **Camera** — add your first RTSP camera (optional, can skip)
5. **Confirm** — review and complete setup

> **Note:** The review step masks the database password. The wizard is permanently locked after completion; contact the developer if you need to reset.

### Setting the Active Event (IMPORTANT)

Camera detections are only recorded as attendance for the **active event**. Before a service or
gathering begins, an admin must set it:

1. Go to **Events** (run **Sync** first if the event isn't listed)
2. Tap **Set Active** on the event that's happening now — it shows a **LIVE** badge
3. When the service ends, tap **clear** (or set the next event)

> If no active event is set, a banner appears on the Tasks page and **detections are not saved
> to attendance**. Always set the active event before the doors open.

### Managing Cameras

Go to **Settings** → Cameras section.

| Action | How |
|--------|-----|
| Add camera | Click "Add", enter name and RTSP URL (must start with `rtsp://` or `rtsps://`) |
| Delete camera | Click "Delete" next to a camera |
| Reconnect | Click "Reconnect" if a camera goes offline |
| Preview frame | Click "Preview" to capture a single live JPEG frame in a modal |

**Camera settings:**
- **Name** — descriptive label (e.g., "Main Entrance")
- **RTSP URL** — stream address (e.g., `rtsp://user:pass@192.168.1.100:554/stream1`)
- **Zone Label** — optional location tag (e.g., "Lobby", "Balcony")
- **FPS** — frames per second to process (default: 1; higher = more CPU)
- **Health check** — enable to track camera online/offline status

### Managing Users

Go to **Settings** → **Manage Users**.

- **Add Volunteer** — enter name, email, role, and temporary password (complexity rules enforced inline)
- **Deactivate** — disable an account without deleting history
- **Reset Password** — generate a one-time reset link. Copy it and share it securely with the user. The link expires in 24 hours.

> Only admins can create accounts. There is no self-registration.

### PIT Queue

The **PIT** (Problem Identification & Tracking) queue contains faces that were skipped 4+ times by 2+ different volunteers. Access it from the **More** menu (admin).

| Action | When to use |
|--------|-------------|
| **Enroll** | Real person — add them to face recognition training |
| **Delete** | False detection or duplicate — remove permanently |
| **Non-Person** | Not a face (poster, shadow, reflection) — discard |

### System Settings

Go to **Settings** to configure the system. Sensitive values (API keys, secrets) are masked with `********` in the display.

| Section | What you can change |
|---------|---------------------|
| **Appearance** | Toggle dark / light mode (persisted across sessions) |
| **Safe Mode** | Pause all face recognition instantly |
| **Cameras** | Add, preview, reconnect, or delete cameras |
| **Admin shortcuts** | Navigate to User Management, Attendance Push, Analytics Dashboard |
| **Recognition & Queue** | Edit tunables (see below) |
| **System** | Update database and Redis connection strings |

### Recognition & Queue Tunables

In the **Recognition & Queue** section of Settings, click **Edit Tunables** to adjust:

| Tunable | Default | Description |
|---------|---------|-------------|
| Auto-log threshold | 0.98 | Similarity ≥ this → attendance logged without review |
| Single-approval threshold | 0.91 | Similarity ≥ this → 1-volunteer review |
| Queue hard limit | 500 | Stop camera ingestion above this many pending tasks |
| Queue resume limit | 400 | Resume ingestion once pending falls below this |
| Dedup window (s) | 30 | Ignore duplicate detections within this window |
| Task expiry (days) | 31 | Unresolved tasks expire after this many days |
| Face retention (days) | 90 | Untrained snapshots deleted after this many days |
| Access token expiry (min) | 15 | JWT access token lifetime |
| Refresh token expiry (days) | 7 | Session cookie lifetime |
| Allowed OAuth domain | lightnc.org | Restrict Google sign-in to this email domain |
| Enable Google OAuth | false | Allow/disallow Google sign-in |

### Attendance Push

Go to **Settings** → **Attendance & CiviCRM Push**.

1. Select an event from the dropdown
2. Click **Preview Push** — see a diff of who will be sent to CiviCRM and any duplicate warnings
3. Click **Push X Records to CiviCRM** — queues records for background processing

**Dead Letter Queue** tab — shows records that failed to push after 3 attempts. Click the retry icon to reset a record for another attempt.

### Analytics Dashboard

Go to **Settings** → **Analytics Dashboard**.

Shows four real-time charts:
- **Attendance per event** — headcount for each recent event
- **Daily detections** — total vs resolved over the last 14 days
- **Recognition tier distribution** — breakdown of auto/1-vol/2-vol/unknown detections
- **Volunteer performance** — points and task breakdown per person

**CSV Export** buttons at the top let you download:
- `attendance.csv` — all confirmed attendance records
- `logs.csv` — up to 5,000 recent system log entries

### Logs

The **Logs** page (More menu → Logs) shows paginated system activity: face detections, volunteer actions, CiviCRM push attempts, and camera events. Scroll or use pagination to browse older entries.

---

## Appearance

The app supports **dark mode** and **light mode**. Toggle it in **Settings** → Appearance. Your preference is saved and survives page reloads. On first visit the app follows your device's system preference.

---

## Installing as an App (PWA)

Project Seraphim is a full Progressive Web App. You can install it on your home screen for a native app-like experience:

**Android (Chrome):**
1. Open the app in Chrome
2. Tap the three-dot menu → "Add to Home Screen" or "Install app"
3. Tap "Install"

**iPhone/iPad (Safari):**
1. Open the app in Safari
2. Tap the Share button → "Add to Home Screen"
3. Tap "Add"

The app will work offline for browsing cached pages, and uses network-first fetching for API calls.

---

## Troubleshooting

### Can't log in
- Check that caps lock is off
- Ask an admin to verify your account is active
- If using Google login, ensure you're signing in with the correct Google account and OAuth is enabled
- If you see "Auth not yet configured", the app may need its setup wizard completed

### No tasks appearing
- Check the Safe Mode banner at the top — recognition may be paused
- Check the Queue Saturated banner — camera ingestion may be paused
- Verify at least one camera is streaming in Settings

### Tasks show wrong names / "member:123"
- This should no longer happen. If you see it, report it — it means the database member sync may be out of date. An admin can run **Sync** on the Members page.

### Camera offline
- Check the RTSP URL in Settings (must start with `rtsp://`)
- Click **Preview** to test if a single frame can be captured
- Try **Reconnect**
- Verify the camera is powered and reachable on the network

### Attendance not showing in CiviCRM
- Check CiviCRM credentials are correct in Settings (under System → Edit Connection Settings or Tunables)
- Go to Logs and look for push-related entries
- Check the Dead Letter Queue tab in Attendance Push
- Retry failed records individually from the Dead Letter Queue

### System feels slow
- Check the Analytics Dashboard → Daily Detections chart for an abnormal backlog
- The queue worker may be overloaded; check Logs for errors
- Compreface may be slow — check your external Compreface server's health

---

## FAQ

**Q: Is my face data safe?**
A: Yes. All face processing happens on the church's own server (Unraid). Unprocessed face images are automatically deleted after 90 days (configurable). Enrolled faces are kept only for recognition.

**Q: Can I use this on my phone?**
A: Yes. The app is designed mobile-first (375px baseline). Install it to your home screen as a PWA for the best experience.

**Q: What happens if I skip a task?**
A: The task returns to the queue for another volunteer. After 4 total skips from 2+ different volunteers, it moves to the admin PIT queue.

**Q: Why do some tasks need 2 approvals?**
A: Low-confidence or unknown matches require extra verification. Each volunteer can only act once — two different people must confirm the match.

**Q: Can I change my password?**
A: Ask an admin to generate a password reset link. The link expires in 24 hours.

**Q: Why does the Google sign-in button sometimes not appear?**
A: It only appears when the admin has enabled Google OAuth in Settings. It's disabled by default.

**Q: What is the mock camera?**
A: A fake RTSP stream used for testing without real hardware. Useful for training sessions.

**Q: Who do I contact for help?**
A: Reach out to your system admin or the developer (John Atienza).

---

*Last updated: 2026-06-02*
