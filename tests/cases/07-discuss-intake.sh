#!/usr/bin/env bash
# Case 07 — discuss-mode intake pins (mc-discuss wiring; design: docs/design-mc-discuss.md).
# Pins SKILL §2 (discuss): the anti-yes-man interrogation contract (≥3 named challenges per
# round, strongest objection first, question caps), the read-only scout scan and its hard
# machine-safety boundary, the post-reality-check fork round, the idea-note grammar (literal
# unbolded Idea: line, defaults on open questions, riskiest-first risks), and the stop rules
# (no wall state; vault-unreachable stops before the idea note).
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$ROOT" || exit 1
SKILL="plugins/mission-control/skills/mission-control/SKILL.md"
FAIL=0
fail() { echo "  07: $*" >&2; FAIL=1; }

[ -f "$SKILL" ] || { echo "  07: SKILL.md missing" >&2; exit 1; }

# §-section extractor (case 06's): body of the section whose header starts with "## <marker>",
# up to the next "## " header. index()==1 keeps the anchor byte-safe.
sec() { awk -v m="## $1" 'index($0, m) == 1 {f=1; next} /^## /{f=0} f' "$SKILL"; }
S2=$(sec "§2")
[ -n "$S2" ] || { fail "no §2 section found in SKILL.md"; exit 1; }

# --- the interrogation contract ---------------------------------------------------------
# 1. Anti-yes-man quota: every round attacks at least three named assumptions, strongest first.
printf '%s\n' "$S2" | grep -Fq -- 'at least three named challenges' \
  || fail "§2 lacks the anti-yes-man quota ('at least three named challenges' per round)"
printf '%s\n' "$S2" | grep -Fq -- 'strongest objection first' \
  || fail "§2 lacks the ordering rule ('strongest objection first')"

# 2. Restatement with testable success conditions, confirmed inside round 1's batch.
printf '%s\n' "$S2" | grep -Fq -- 'numbered, testable success conditions' \
  || fail "§2 lacks the testable-success-conditions restatement rule"
printf '%s\n' "$S2" | grep -Fq -- 'search-before-write' \
  || fail "§2 lacks the search-before-write duplicate guard"

# 3. Question caps on both questioning stages.
printf '%s\n' "$S2" | grep -Fq -- 'max 2, max 4 questions each' \
  || fail "§2 lacks the interrogation question caps (max 2 rounds, max 4 questions each)"
printf '%s\n' "$S2" | grep -Fq -- 'exactly 1, max 4 forks' \
  || fail "§2 lacks the post-reality-check round contract (exactly 1, max 4 forks)"

# --- the reality scan --------------------------------------------------------------------
# 4. Scout topology and its hard read-only boundary (machine-safety: scouts never run gates).
printf '%s\n' "$S2" | grep -Fq -- '2-5 read-only scouts' \
  || fail "§2 lacks the scout topology ('2-5 read-only scouts')"
printf '%s\n' "$S2" | grep -Fq -- 'NEVER run tests, gates, builds, or installs' \
  || fail "§2 lacks the scouts' hard boundary ('NEVER run tests, gates, builds, or installs')"
printf '%s\n' "$S2" | grep -Fq -- 'a finding without an evidence anchor is discarded' \
  || fail "§2 lacks the evidence-anchor rule ('a finding without an evidence anchor is discarded')"

# --- the idea note -------------------------------------------------------------------------
# 5. The literal Idea: line plan copies verbatim into Objective:.
printf '%s\n' "$S2" | grep -Fq -- 'literal unbolded `Idea:' \
  || fail "§2 lacks the literal unbolded \`Idea:\` line rule (plan copies it into Objective:)"
printf '%s\n' "$S2" | grep -Fq -- 'every open question carries a default' \
  || fail "§2 lacks the open-questions default rule"
printf '%s\n' "$S2" | grep -Fq -- 'riskiest assumption first' \
  || fail "§2 lacks the risks ordering ('riskiest assumption first')"

# --- the stop rules ------------------------------------------------------------------------
# 6. No wall state before forge, and the vault-unreachable stop.
printf '%s\n' "$S2" | grep -Fq -- 'never mints wall state' \
  || fail "§2 lacks the no-wall-state rule (rows are born at forge)"
printf '%s\n' "$S2" | grep -Fq -- 'an unrecorded refinement is lost exactly where it matters most' \
  || fail "§2 lacks the vault-unreachable stop rule"

exit $FAIL
