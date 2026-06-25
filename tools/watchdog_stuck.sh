#!/usr/bin/env bash
# Sprint-loop watchdog: detect a STUCK in-flight senpai build.
#
# A build is "stuck" when the workflow we believe is running (the runId tagged
# `🔄 IMPLEMENT building (wf_...)` in SPRINT-LOOP-LOG.md) has written NOTHING to
# its transcript dir for longer than IDLE_THRESHOLD_MIN. A hung tool call (e.g. a
# blocking server boot, the exact failure that froze S04's devops agent for 73
# min) shows up here as a stale freshest-mtime while the UI still says "running".
#
# Exit codes:  0 = healthy/active or no build in flight   2 = STUCK (act on it)
# Prints a one-line verdict + the stuck runId + idle minutes for the cron to act.

set -uo pipefail

REPO="/home/user/seraphim"
LOG="$REPO/docs/superpowers/SPRINT-LOOP-LOG.md"
WF_DIR="/root/.claude/projects/-home-user-seraphim/6a038f3d-7ef8-54a3-a3b5-1c24d3e8fc8f/subagents/workflows"
IDLE_THRESHOLD_MIN="${IDLE_THRESHOLD_MIN:-25}"   # minutes of silence => stuck

# 1. Extract the in-flight build runId from the log's "IMPLEMENT building (wf_...)" marker.
#    Tolerant of markdown noise (backticks/asterisks) between "building" and the runId.
runid="$(grep -E 'IMPLEMENT building' "$LOG" 2>/dev/null \
          | grep -oE 'wf_[a-z0-9-]+' | tail -1)"

if [ -z "${runid:-}" ]; then
  echo "WATCHDOG: HEALTHY — no build marked in-flight in the sprint log."
  exit 0
fi

dir="$WF_DIR/$runid"
if [ ! -d "$dir" ]; then
  echo "WATCHDOG: HEALTHY — in-flight runId $runid has no transcript dir yet (just launched)."
  exit 0
fi

# 2. Freshest file mtime in the workflow dir (epoch seconds).
newest="$(find "$dir" -type f -printf '%T@\n' 2>/dev/null | sort -n | tail -1 | cut -d. -f1)"
if [ -z "${newest:-}" ]; then
  echo "WATCHDOG: HEALTHY — $runid dir empty, nothing to judge."
  exit 0
fi

now="$(date +%s)"
idle_min=$(( (now - newest) / 60 ))

# 3. Idle time is the reliable signal. Fresh writes => actively building, period.
#    (Intermediate agents emit "result" journal lines too, so a journal grep is
#    NOT a trustworthy "done" marker — only silence-vs-activity is.)
if [ "$idle_min" -lt "$IDLE_THRESHOLD_MIN" ]; then
  echo "WATCHDOG: ACTIVE — $runid wrote ${idle_min}m ago (< ${IDLE_THRESHOLD_MIN}m threshold). Building normally."
  exit 0
fi

# Idle past threshold: either (a) the workflow finished and the orchestrator hasn't
# committed yet, or (b) a tool call is hung (the S04 devops freeze). The cron-fired
# session must investigate — check git status, the workflow result, then either
# commit-and-advance or resume-from-journal/kill. Flag it either way.
echo "WATCHDOG: STALE — $runid idle ${idle_min}m (>= ${IDLE_THRESHOLD_MIN}m) while still marked in-flight."
echo "STUCK_RUNID=$runid IDLE_MIN=$idle_min"
exit 2
