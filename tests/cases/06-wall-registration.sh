#!/usr/bin/env bash
# Case 06 — plan-mode Wall registration + program-note grammar (wall-signal-panel W1-L2;
# re-pinned for contract v2 at 1.11.0: per-poll registration, 9-cell deps rows).
# Pins SKILL §3 step 7 (idempotent ~/.mc-wall/wall.json registration at plan time, with the
# per-poll no-restart visibility + state-probe verification and the skip-when-absent rule)
# and the §1 note grammar the Wall's notes.py parser actually reads (9-cell deps rows,
# legacy 8-cell compatibility, path-token repo cell, no pre-forge placeholder rows,
# unbolded objective, fail-visible defects, fork-disclosure display rule).
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$ROOT" || exit 1
SKILL="plugins/mission-control/skills/mission-control/SKILL.md"
FAIL=0
fail() { echo "  06: $*" >&2; FAIL=1; }

[ -f "$SKILL" ] || { echo "  06: SKILL.md missing" >&2; exit 1; }

# §-section extractor: prints the body of the section whose header starts with "## <marker>",
# up to the next "## " header. index()==1 keeps the anchor byte-safe (no regex unicode games).
sec() { awk -v m="## $1" 'index($0, m) == 1 {f=1; next} /^## /{f=0} f' "$SKILL"; }
S1=$(sec "§1"); S3=$(sec "§3")
[ -n "$S1" ] || { fail "no §1 section found in SKILL.md"; exit 1; }
[ -n "$S3" ] || { fail "no §3 section found in SKILL.md"; exit 1; }

# --- §3 step 7: plan-mode Wall registration -------------------------------------------
# 1. The registration targets the wall's machine config.
printf '%s\n' "$S3" | grep -Fq -- '~/.mc-wall/wall.json' \
  || fail "§3 never names ~/.mc-wall/wall.json (plan-mode registration target)"

# 2. The append is idempotent (program entry).
printf '%s\n' "$S3" | grep -Fq -- 'if no entry with that program name exists' \
  || fail "§3 lacks the idempotent programs[] append ('if no entry with that program name exists')"
printf '%s\n' "$S3" | grep -Fq -- 'idempotently' \
  || fail "§3 does not state the registration is idempotent"

# 3. Repos are registered too.
printf '%s\n' "$S3" | grep -Fq -- 'repos[]' \
  || fail "§3 lacks the repos[] append for worked repos"

# 4. Timestamped backup before the edit.
printf '%s\n' "$S3" | grep -Fq -- 'timestamped backup' \
  || fail "§3 lacks the timestamped backup before wall.json is touched"

# 5. Registration is per-poll (contract v2): visible on the next poll, NO restart.
printf '%s\n' "$S3" | grep -Fq -- 'per-poll' \
  || fail "§3 lacks the per-poll registration rule (contract v2)"
printf '%s\n' "$S3" | grep -Fq -- 'NO restart' \
  || fail "§3 lacks the explicit NO-restart statement for registration"
printf '%s\n' "$S3" | grep -Fq -- 'BOOT only' \
  && fail "§3 still carries the stale BOOT-only registration text (superseded by per-poll)"

# 6. Verification via the state probe.
printf '%s\n' "$S3" | grep -Fq -- '<token>/state' \
  || fail "§3 lacks the curl state-probe verification (localhost:<port>/<token>/state)"

# 7. Wall not installed: skip and record, never create the file.
printf '%s\n' "$S3" | grep -Fq -- 'wall not installed' \
  || fail "§3 lacks the skip-when-absent rule's 'wall not installed' recording"
printf '%s\n' "$S3" | grep -Fq -- 'NEVER create the file' \
  || fail "§3 lacks the NEVER-create-the-file guard for absent ~/.mc-wall/wall.json"

# --- §1: program-note grammar for the Wall --------------------------------------------
# 8. Prompt-log rows carry exactly 9 cells — the variant-C deps form (contract v2).
printf '%s\n' "$S1" | grep -Fq -- 'EXACTLY 9 cells' \
  || fail "§1 lacks the 9-cell prompt-log row rule (variant C: trailing deps cell)"
printf '%s\n' "$S1" | grep -Fq -- 'session/MR artifacts | status | deps' \
  || fail "§1 lacks the 9-cell column list (… status | deps)"
printf '%s\n' "$S1" | grep -Fq -- 'still parse unchanged' \
  || fail "§1 lacks the legacy-header compatibility rule (8-cell A and B forms still parse unchanged)"
printf '%s\n' "$S1" | grep -Fq -- 'silently skipped' \
  && fail "§1 still claims malformed rows are silently skipped (fail-visible since contract v2)"
printf '%s\n' "$S1" | grep -Fq -- 'parse defect' \
  || fail "§1 lacks the fail-visible parse-defect rule (malformed rows surface naming note path + defect)"

# 9. The deps cell carries row_ids (comma/space-separated) or — when none.
printf '%s\n' "$S1" | grep -Fq -- 'comma/space-separated' \
  || fail "§1 lacks the deps-cell format rule (comma/space-separated row_ids)"
printf '%s\n' "$S1" | grep -Fq -- '`—` when the lane depends on nothing' \
  || fail "§1 lacks the empty-deps rule (— when the lane depends on nothing)"

# 10. The repo/branch cell needs a path token (bare repo name parses repo=None).
printf '%s\n' "$S1" | grep -Fq -- 'path token' \
  || fail "§1 lacks the path-token rule for the repo/branch cell (repo=None without it)"

# 11. No pre-forge placeholder rows (status vocabulary has no 'planned' state).
printf '%s\n' "$S1" | grep -Fq -- 'pre-forge placeholder rows' \
  || fail "§1 lacks the no-pre-forge-placeholder-rows rule"
printf '%s\n' "$S1" | grep -Fq -- '"planned" state' \
  || fail "§1 does not state the status vocabulary has no \"planned\" state"

# 12. Objective line unbolded (parser matches the literal prefix).
printf '%s\n' "$S1" | grep -Fq -- 'unbolded `Objective:`' \
  || fail "§1 lacks the unbolded-Objective rule (**Objective:** does not parse)"

# 13. Fork disclosure display rule for wall-declared programs (operator ruling 2026-09-22).
printf '%s\n' "$S1" | grep -Fq -- 'evidence prose' \
  || fail "§1 lacks the fork-disclosure evidence-prose rule for wall-declared programs"
printf '%s\n' "$S1" | grep -Fq -- 'renders as a lane' \
  || fail "§1 lacks the why of the display rule (an FX row renders as a lane)"

exit $FAIL
