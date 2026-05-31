# Project Seraphim — User Guide

**Light of the World Worldwide Ministries — North Caloocan (LNC)**

A step-by-step guide for volunteers and administrators using the LNC Attendance System.

---

## Table of Contents

- [Getting Started](#getting-started)
- [Volunteer Guide](#volunteer-guide)
  - [Logging In](#logging-in)
  - [Reviewing Tasks](#reviewing-tasks)
  - [Understanding Confidence Tiers](#understanding-confidence-tiers)
  - [Leaderboard](#leaderboard)
  - [Events & Attendees](#events--attendees)
- [Admin Guide](#admin-guide)
  - [Setup Wizard](#setup-wizard)
  - [Managing Cameras](#managing-cameras)
  - [Managing Users](#managing-users)
  - [PIT Queue](#pit-queue)
  - [System Settings](#system-settings)
  - [Attendance Push](#attendance-push)
  - [Logs](#logs)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)

---

## Getting Started

### What is Project Seraphim?

Project Seraphim is a facial recognition attendance system for LNC. It uses cameras at church entrances to detect faces, matches them against enrolled members, and logs attendance to CiviCRM. Volunteers review uncertain matches to ensure accuracy.

### Who uses it?

| Role | What they do |
|------|-------------|
| **Volunteer** | Review face recognition tasks, confirm/edit/skip matches, view leaderboard |
| **Admin** | Everything a volunteer does, plus: manage cameras, users, settings, push attendance to CiviCRM, resolve PIT queue items |

### First Login

1. Open the app in your browser (ask your admin for the URL)
2. Enter your email and password
3. If your admin enabled Google OAuth, you can also click "Sign in with Google"

---

## Volunteer Guide

### Logging In

- Use the email and password provided by your admin
- If you forget your password, ask an admin to generate a reset link
- The system will keep you logged in for 7 days

### Reviewing Tasks

The **Tasks** page is your main workspace. You will see face detection cards that need your review.

#### Task Card Layout

Each card shows:
- **Face thumbnail** — the detected face from the camera
- **Matched name** — who the system thinks this is (or "Unknown")
- **Confidence %** — how sure the AI is (e.g., 97.5%)
- **Tier badge** — tells you how many approvals are needed:
  - **Auto** (100%) — already confirmed automatically, you won't see these
  - **1-Vol** (91-99%) — needs 1 volunteer approval
  - **2-Vol** (< 90%) — needs 2 volunteer approvals
  - **Unknown** — no match found, needs 2 approvals
- **Camera name** — which camera detected this face
- **Time** — when the detection happened

#### Actions

| Button | When to use | What happens |
|--------|-------------|--------------|
| **Confirm** (gold) | The matched name is correct | Approval recorded. If enough approvals reached, attendance is logged. |
| **Edit** | The system matched the wrong person | Search for the correct member, then confirm. |
| **Add** | This is a new person not in the system | Search for the member in CiviCRM, link them, and confirm. |
| **Skip** | You can't identify this face | Choose a reason (face unclear, person unknown, not a face, will ask later). The task goes to another volunteer. |

> **Cooldown:** After each action, you must wait 3 seconds before acting on another task. This prevents accidental double-clicks.

#### Queue Status Banners

You may see banners at the top of the Tasks page:
- **Safe Mode** — recognition is paused by an admin
- **Queue Saturated** — too many pending tasks; camera ingestion is paused temporarily

### Understanding Confidence Tiers

The AI assigns a confidence score to every face match:

| Tier | Score | Approvals Needed | Your Action |
|------|-------|-----------------|-------------|
| Auto | 98-100% | 0 (automatic) | None — already logged |
| 1-Vol | 91-97.9% | 1 volunteer | Click Confirm if correct |
| 2-Vol | Below 91% | 2 volunteers | Click Confirm if correct |
| Unknown | No match | 2 volunteers | Use Edit or Add to identify |

### Leaderboard

The **Ranking** page shows volunteer standings:
- **Points** — earned for confirms, edits, and adds
- **Tasks Completed** — total actions taken
- **Accuracy** — percentage of correct confirmations

Points:
| Action | Points |
|--------|--------|
| Confirm or Add | +1 |
| Edit (name change) | +2 |
| Incorrect confirmation (found by audit) | -3 |

### Events & Attendees

- **Events** — view upcoming church events synced from CiviCRM
- **Attendees** — browse enrolled members with their face thumbnails and sample counts

---

## Admin Guide

### Setup Wizard

The first time the system starts, you must complete the setup wizard:

1. **Database** — confirm PostgreSQL connection
2. **External Services** — enter Compreface URL and API key, plus CiviCRM credentials (optional)
3. **Admin Account** — create the first admin user
4. **Camera** — add your first RTSP camera (optional, can skip)
5. **Confirm** — review and finish

After setup completes, the wizard is permanently locked. To re-run setup, you must reset the database.

### Managing Cameras

Go to **Settings** → scroll to the Cameras section.

| Action | How |
|--------|-----|
| Add camera | Click "Add Camera", enter name and RTSP URL |
| Edit camera | Click the pencil icon next to a camera |
| Delete camera | Click the trash icon |
| Reconnect | Click the refresh icon if a camera goes offline |
| Preview | Click the eye icon to grab a single JPEG frame |

**Camera settings:**
- **Name** — descriptive label (e.g., "Main Entrance")
- **RTSP URL** — stream address (e.g., `rtsp://user:pass@192.168.1.100:554/stream1`)
- **Zone Label** — optional location tag
- **FPS** — frames per second to process (default: 1)

### Managing Users

Go to **Settings** → User Management.

- **Add Volunteer** — enter email and temporary password. The volunteer can log in immediately.
- **Deactivate** — disable an account without deleting data
- **Reset Password** — generate a password reset link to share with the user

> Only admins can create accounts. There is no self-registration.

### PIT Queue

The **PIT** (Problem Identification & Tracking) queue contains faces that volunteers skipped 4+ times from 2+ different volunteers.

Go to the **PIT** page to review these items:

| Action | When to use |
|--------|-------------|
| **Enroll** | This is a real person — add them to face recognition |
| **Delete** | False detection or duplicate — remove permanently |
| **Non-Person** | Not a face (poster, shadow, etc.) — discard |

### System Settings

Go to **Settings** to configure:

| Setting | Description |
|---------|-------------|
| **Safe Mode** | Pause all face recognition instantly. Useful during maintenance or privacy concerns. |
| **Compreface URL** | Address of your external Compreface server |
| **Compreface API Key** | Authentication key for Compreface |
| **CiviCRM URL / Keys** | Connection details for CiviCRM sync |
| **Google OAuth** | Enable/disable Google sign-in |
| **JWT Expiry** | Access token lifetime (default: 15 minutes) |
| **Queue Limits** | Hard limit for pending tasks (default: 500) |
| **Face Retention** | Days to keep unprocessed face images (default: 31) |

### Attendance Push

After services or events, push confirmed attendance records to CiviCRM:

1. Go to **Settings** → Attendance Push
2. Click **Preview** to see a diff of what will be pushed
3. Click **Push** to queue records for background processing

If a push fails 3 times, it goes to the **Dead Letter Queue** where you can retry manually.

### Logs

The **Logs** page shows all system activity:
- Face detections
- Volunteer actions (confirm, edit, skip, etc.)
- CiviCRM push attempts
- Camera status changes

Use filters to narrow by date, action type, or volunteer.

---

## Troubleshooting

### Can't log in
- Check that caps lock is off
- Ask an admin to verify your account is active
- If using Google OAuth, ensure you're signing in with the correct Google account

### No tasks appearing
- Check if Safe Mode is enabled (ask an admin)
- Check if the queue is saturated — camera ingestion may be paused
- Verify cameras are streaming in Settings

### Tasks look wrong (bad matches)
- Use **Edit** to correct the matched name
- If the face is unclear, use **Skip** with reason "Face unclear"
- If it's not a real face, use **Skip** with reason "Not a face"

### Camera offline
- Check the camera's RTSP URL in Settings
- Try the **Reconnect** button
- Verify the camera is powered and on the network
- Use **Preview** to test if a single frame can be captured

### Attendance not showing in CiviCRM
- Check that CiviCRM credentials are correct in Settings
- Go to Logs and filter by "push" actions
- Check the Dead Letter Queue for failed pushes
- Try a manual retry from the Dead Letter Queue

### System feels slow
- The queue worker may be backed up — check Logs for errors
- Compreface may be overloaded — check your external Compreface server
- Safe Mode may have been toggled — check Settings

---

## FAQ

**Q: Is my face data safe?**
A: Yes. Face images are stored on the church's local server (Unraid). Unprocessed images are automatically deleted after 31 days. Only enrolled member faces are kept for recognition.

**Q: Can I use this on my phone?**
A: Yes. The app is designed mobile-first and works best on phones and tablets. Add it to your home screen for an app-like experience.

**Q: What happens if I skip a task?**
A: The task goes back into the queue for another volunteer. If 4 different volunteers skip it from 2+ people, it goes to the admin PIT queue.

**Q: Why do some tasks need 2 approvals?**
A: Low-confidence matches (below 90%) require extra verification to prevent errors. This is the "dual-approval" system.

**Q: Can I change my password?**
A: Ask an admin to generate a password reset link for you.

**Q: What is the mock camera?**
A: It's a fake RTSP stream used for testing when no real cameras are available. Admins can enable it for training purposes.

**Q: Who do I contact for help?**
A: Reach out to your system admin or the developer (John Atienza).

---

*Last updated: 2026-05-31*
