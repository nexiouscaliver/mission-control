#!/usr/bin/env bash
# Case 02 — interface-doc citation resolution against the pinned regenloop fixture
# (J2 risk-#1 mitigation; council repair R4: bracketed-key grammar per T1's actual format,
# e.g. "[run L157]", "[o-state L81]", "[ship L1200]", "[GR L66]", "[gt L34]").
# Keys: run/ship/GR/doc/rep -> the five skills/*/SKILL.md fixtures (GR = the gate-runner
# SKILL.md, NOT gate_runner.py); o-state/gt -> docs/*.md; state.py/budget.py/guard.py are
# pointer-keys into RL scripts/ — exempt from fixture resolution (never snapshotted), their
# line cites checked against the LIVE plugin only (WARN class, drift signal not blocking).
# Step 4 pairs mechanism literals doc<->fixture (both sides must carry each literal).
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$ROOT" || exit 1
DOC="plugins/mission-control/skills/mission-control/references/regenloop-interface.md"
LIVE_RL="$HOME/.zcode/cli/plugins/cache/regenloop/regenloop/1.3.1"
FAIL=0
fail() { echo "  02: $*" >&2; FAIL=1; }
warn() { echo "  02: WARN $*" >&2; }

[ -f "$DOC" ] || { echo "  02: interface doc missing (ships in v1.3.0)" >&2; exit 1; }

# 0. Pin check: 'Derived from regenloop <ver>' matches exactly one fixture dir.
VER=$(grep -m1 -oE 'Derived from regenloop \*{0,2}[0-9.]+' "$DOC" | grep -oE '[0-9.]+$')
[ -n "$VER" ] || fail "no 'Derived from regenloop <ver>' pin in doc header"
FIX="tests/fixtures/regenloop-$VER"
[ -d "$FIX" ] || fail "doc pinned to regenloop $VER but fixture $FIX missing (snapshot or re-derive)"

# 0b. R11/R4c fixture-target existence: every plugin surface the doc points at must be
#     snapshotted — kills the pointer-to-nowhere class (named target, no fixture file).
for t in skills/gate-runner/SKILL.md skills/regenloop-doctor/SKILL.md \
         skills/regenloop-report/SKILL.md knowledge/INDEX.md \
         knowledge/cards/regenloop-run.md knowledge/cards/regenloop-ship.md \
         knowledge/cards/scripts.md; do
  [ -f "$FIX/$t" ] || fail "R11/R4c fixture target missing: $FIX/$t (doc points at it — snapshot it)"
done

# 1. Key -> fixture file map (R4c: GR is the gate-runner SKILL; scripts stay pointer-only).
pathfor() { case "$1" in
  run) echo "skills/regenloop-run/SKILL.md";;
  ship) echo "skills/regenloop-ship/SKILL.md";;
  GR) echo "skills/gate-runner/SKILL.md";;
  doc) echo "skills/regenloop-doctor/SKILL.md";;
  rep) echo "skills/regenloop-report/SKILL.md";;
  o-state) echo "docs/orchestrator-state.md";;
  gt) echo "docs/gate-tuning.md";;
  *) echo "";; esac; }
is_pointer() { case "$1" in state.py|budget.py|guard.py) return 0;; *) return 1;; esac; }
live_script() {  # pointer-key -> live plugin script (regenloop_<key> is the real name)
  for f in "$LIVE_RL/scripts/regenloop_$1" "$LIVE_RL/scripts/$1"; do
    [ -f "$f" ] && { echo "$f"; return 0; }
  done; return 1; }

# 2. Every bracketed citation key resolves; every L<number> stays within its own target's
#    bounds (multi-key tokens split on ';' so each key's cites hit the right file).
TOKENS=$(grep -oE '\[(run|ship|GR|doc|rep|o-state|gt|state\.py|budget\.py|guard\.py)[^]]*\]' "$DOC" | sort -u)
[ -n "$TOKENS" ] || fail "no bracketed citation tokens found (grammar: [run L157] style)"
while IFS= read -r tok; do
  segs=$(printf '%s' "$tok" | tr ';' '\n')
  while IFS= read -r seg; do
    key=$(printf '%s' "$seg" | sed 's/^[[:space:]]*//; s/^\[//; s/\].*$//; s/[[:space:]].*//')
    [ -n "$key" ] || continue
    if is_pointer "$key"; then
      # Pointer-key: exempt from fixture resolution; live-plugin line check is WARN-only.
      if live=$(live_script "$key"); then
        lines=$(wc -l < "$live" | tr -d ' ')
        for ln in $(printf '%s' "$seg" | grep -oE 'L[0-9]+' | tr -d 'L'); do
          [ "$ln" -le "$lines" ] || warn "pointer-key '$seg' cites L$ln but $(basename "$live") has $lines lines (live-plugin drift — re-derive)"
        done
      else
        printf '%s' "$seg" | grep -qE 'L[0-9]+' && warn "pointer-key '$key' cites lines but no live script under $LIVE_RL/scripts/ (re-derive)"
      fi
      continue
    fi
    tgt=$(pathfor "$key")
    [ -n "$tgt" ] || { fail "unmapped citation key in '$tok' (segment '$seg')"; continue; }
    [ -f "$FIX/$tgt" ] || { fail "'$seg' -> $tgt not in fixture (snapshot it or fix the citation)"; continue; }
    lines=$(wc -l < "$FIX/$tgt" | tr -d ' ')
    for ln in $(printf '%s' "$seg" | grep -oE 'L[0-9]+' | tr -d 'L'); do
      [ "$ln" -le "$lines" ] || fail "'$seg' cites L$ln but $tgt has $lines lines (drift or bad cite)"
    done
  done <<SEGEOF
$segs
SEGEOF
done <<EOF
$TOKENS
EOF

# 4. Paired-literal checks (R4d/C4, post-B P1-1): each mechanism literal must appear in BOTH
#    the interface doc AND the mapped fixture file — the doc's one-liners stay pinned to the
#    plugin's real mechanics. Deliberately all 8 pairs; do not weaken or re-scope.
while IFS= read -r pair; do
  [ -n "$pair" ] || continue
  lit=${pair%@*}; key=${pair##*@}
  tgt=$(pathfor "$key"); [ -n "$tgt" ] || { fail "paired literal '$lit' has unmapped key '$key'"; continue; }
  grep -Fq -- "$lit" "$DOC"      || fail "paired literal '$lit' missing from interface doc (drift or weakened prose)"
  grep -Fq -- "$lit" "$FIX/$tgt" || fail "paired literal '$lit' missing from fixture $tgt (plugin drift — re-derive doc)"
done <<EOF
MAX_ROUNDS = 3@ship
--cap-gate 3@run
CAP_RUN@run
--max-resets-per-gate 1@run
1800@run
validate-queue@run
| date | slug | outcome |@o-state
16 KB@o-state
EOF

exit $FAIL
