#!/usr/bin/env bash
# Case 02 — interface-doc citation resolution against the pinned regenloop fixture
# (J2 risk-#1 mitigation; council repair R4: bracketed-key grammar per T1's actual format,
# e.g. "[run L157]", "[o-state L81]", "[ship L1200]", "[GR L66]", "[tuning L10]").
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$ROOT" || exit 1
DOC="plugins/mission-control/skills/mission-control/references/regenloop-interface.md"
FAIL=0
fail() { echo "  02: $*" >&2; FAIL=1; }

[ -f "$DOC" ] || { echo "  02: interface doc missing (ships in v1.3.0)" >&2; exit 1; }

# 0. Pin check: 'Derived from regenloop <ver>' matches exactly one fixture dir.
VER=$(grep -m1 -oE 'Derived from regenloop \*{0,2}[0-9.]+' "$DOC" | grep -oE '[0-9.]+$')
[ -n "$VER" ] || fail "no 'Derived from regenloop <ver>' pin in doc header"
FIX="tests/fixtures/regenloop-$VER"
[ -d "$FIX" ] || fail "doc pinned to regenloop $VER but fixture $FIX missing (snapshot or re-derive)"

# 1. Key -> fixture file map.
pathfor() { case "$1" in
  run) echo "skills/regenloop-run/SKILL.md";;
  ship) echo "skills/regenloop-ship/SKILL.md";;
  o-state) echo "docs/orchestrator-state.md";;
  tuning) echo "docs/gate-tuning.md";;
  GR) echo "gate_runner.py";;
  *) echo "";; esac; }

# 2. Every bracketed citation key resolves; every L<number> stays within the target's lines.
TOKENS=$(grep -oE '\[(run|ship|o-state|tuning|GR)[^]]*\]' "$DOC" | sort -u)
[ -n "$TOKENS" ] || fail "no bracketed citation tokens found (grammar: [run L157] style)"
while IFS= read -r tok; do
  key=$(printf '%s' "$tok" | sed 's/^\[//;s/[\[ ].*//')
  tgt=$(pathfor "$key")
  [ -n "$tgt" ] || { fail "unmapped citation key in '$tok'"; continue; }
  [ -f "$FIX/$tgt" ] || { fail "'$tok' -> $tgt not in fixture (snapshot it or fix the citation)"; continue; }
  lines=$(wc -l < "$FIX/$tgt" | tr -d ' ')
  for ln in $(printf '%s' "$tok" | grep -oE 'L[0-9]+' | tr -d 'L'); do
    [ "$ln" -le "$lines" ] || fail "'$tok' cites L$ln but $tgt has $lines lines (drift or bad cite)"
  done
done <<EOF
$TOKENS
EOF

exit $FAIL
