#!/usr/bin/env bash
# Case 06 — plan-mode Wall registration + program-note grammar (wall-signal-panel W1-L2).
# Pins SKILL §2 step 6 (idempotent ~/.mc-wall/wall.json registration at plan time, with the
# boot-restart + state-probe verification and the skip-when-absent rule) and the §1 note
# grammar the Wall's notes.py parser actually reads (8-cell rows, path-token repo cell,
# no pre-forge placeholder rows, unbolded objective, fork-disclosure display rule).
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$ROOT" || exit 1
SKILL="plugins/mission-control/skills/mission-control/SKILL.md"
FAIL=0
fail() { echo "  06: $*" >&2; FAIL=1; }

[ -f "$SKILL" ] || { echo "  06: SKILL.md missing" >&2; exit 1; }

# §-section extractor: prints the body of the section whose header starts with "## <marker>",
# up to the next "## " header. index()==1 keeps the anchor byte-safe (no regex unicode games).
sec() { awk -v m="## $1" 'index($0, m) == 1 {f=1; next} /^## /{f=0} f' "$SKILL"; }
S1=$(sec "§1"); S2=$(sec "§2")
[ -n "$S1" ] || { fail "no §1 section found in SKILL.md"; exit 1; }
[ -n "$S2" ] || { fail "no §2 section found in SKILL.md"; exit 1; }

# --- §2 step 6: plan-mode Wall registration -------------------------------------------
# 1. The registration targets the wall's machine config.
printf '%s\n' "$S2" | grep -Fq -- '~/.mc-wall/wall.json' \
  || fail "§2 never names ~/.mc-wall/wall.json (plan-mode registration target)"

# 2. The append is idempotent (program entry).
printf '%s\n' "$S2" | grep -Fq -- 'if no entry with that program name exists' \
  || fail "§2 lacks the idempotent programs[] append ('if no entry with that program name exists')"
printf '%s\n' "$S2" | grep -Fq -- 'idempotently' \
  || fail "§2 does not state the registration is idempotent"

# 3. Repos are registered too.
printf '%s\n' "$S2" | grep -Fq -- 'repos[]' \
  || fail "§2 lacks the repos[] append for worked repos"

# 4. Timestamped backup before the edit.
printf '%s\n' "$S2" | grep -Fq -- 'timestamped backup' \
  || fail "§2 lacks the timestamped backup before wall.json is touched"

# 5. Boot-time config: the restart command is named.
printf '%s\n' "$S2" | grep -Fq -- 'launchctl kickstart -k gui/$(id -u)/ai.zcode.mc-wall' \
  || fail "§2 lacks the wall restart command (launchctl kickstart -k gui/$(id -u)/ai.zcode.mc-wall)"

# 6. Verification via the state probe.
printf '%s\n' "$S2" | grep -Fq -- '<token>/state' \
  || fail "§2 lacks the curl state-probe verification (localhost:<port>/<token>/state)"

# 7. Wall not installed: skip and record, never create the file.
printf '%s\n' "$S2" | grep -Fq -- 'wall not installed' \
  || fail "§2 lacks the skip-when-absent rule's 'wall not installed' recording"
printf '%s\n' "$S2" | grep -Fq -- 'NEVER create the file' \
  || fail "§2 lacks the NEVER-create-the-file guard for absent ~/.mc-wall/wall.json"

# --- §1: program-note grammar for the Wall --------------------------------------------
# 8. Prompt-log rows carry exactly 8 cells (7-cell rows are silently skipped).
printf '%s\n' "$S1" | grep -Fq -- 'EXACTLY 8 cells' \
  || fail "§1 lacks the 8-cell prompt-log row rule (the Wall silently skips 7-cell rows)"

# 9. The repo/branch cell needs a path token (bare repo name parses repo=None).
printf '%s\n' "$S1" | grep -Fq -- 'path token' \
  || fail "§1 lacks the path-token rule for the repo/branch cell (repo=None without it)"

# 10. No pre-forge placeholder rows (status vocabulary has no 'planned' state).
printf '%s\n' "$S1" | grep -Fq -- 'pre-forge placeholder rows' \
  || fail "§1 lacks the no-pre-forge-placeholder-rows rule"
printf '%s\n' "$S1" | grep -Fq -- '"planned" state' \
  || fail "§1 does not state the status vocabulary has no \"planned\" state"

# 11. Objective line unbolded (parser matches the literal prefix).
printf '%s\n' "$S1" | grep -Fq -- 'unbolded `Objective:`' \
  || fail "§1 lacks the unbolded-Objective rule (**Objective:** does not parse)"

# 12. Fork disclosure display rule for wall-declared programs (operator ruling 2026-09-22).
printf '%s\n' "$S1" | grep -Fq -- 'evidence prose' \
  || fail "§1 lacks the fork-disclosure evidence-prose rule for wall-declared programs"
printf '%s\n' "$S1" | grep -Fq -- 'renders as a lane' \
  || fail "§1 lacks the why of the display rule (an FX row renders as a lane)"

exit $FAIL
