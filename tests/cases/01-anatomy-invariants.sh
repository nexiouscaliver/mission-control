#!/usr/bin/env bash
# Case 01 — anatomy invariants (T5; SKELETON_N=14 per council repair R1 — T4's canonical skeleton).
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$ROOT" || exit 1
ANAT="plugins/mission-control/skills/mission-control/references/prompt-anatomy.md"
SKELETON_N=16   # v1.4.0 truth (14 + standing-goal line + session-title line). Deliberate changes update this constant.
FAIL=0
fail() { echo "  01: $*" >&2; FAIL=1; }

[ -f "$ANAT" ] || { echo "  01: anatomy file missing" >&2; exit 1; }

# 1. Skeleton section holds exactly SKELETON_N consecutively-numbered bold items.
SEC=$(awk '/^## The skeleton/{f=1;next} /^## /{f=0} f' "$ANAT")
[ -n "$SEC" ] || fail "no '## The skeleton' section"
N=$(printf '%s\n' "$SEC" | grep -cE '^[0-9]+\. \*\*')
[ "$N" -eq "$SKELETON_N" ] || fail "skeleton has $N numbered items, expected $SKELETON_N (update constant deliberately or fix the drop)"
i=1; while [ $i -le $SKELETON_N ]; do
  printf '%s\n' "$SEC" | grep -qE "^${i}\. \*\*" || fail "skeleton item $i missing or out of order"
  i=$((i+1))
done

# 2. Module bidirectional binding: every situational module named in SKILL.md (via synonyms),
#    every module referenced at least once outside the module table.
SKILL="plugins/mission-control/skills/mission-control/SKILL.md"
MODS=$(awk '/^## Situational modules/{f=1;next} /^## /{f=0} f' "$ANAT" | grep -oE '^\| \*\*[^|]+\*\*' | sed 's/^| \*\*//;s/\*\*$//' | sed 's/ *$//')
[ -n "$MODS" ] || fail "no situational modules parsed from anatomy table"
syn() { case "$1" in
  "Abort path") echo "kill-switch|abort module|Abort path";;
  "Dogfood / canary") echo "dogfood|canary|Dogfood";;
  "Part 1 / Part 2") echo "Part 1|Part 2|merge-pause";;
  "Deploy window") echo "Deploy window|deploy window|deploy —|deploy ->|Pin .* swap";;
  "Release prep") echo "Release prep|release-prep|version bump";;
  "Remediation") echo "[Rr]emediation";;
  "One-shot") echo "[Oo]ne-shot|[Oo]ne shot";;
  *) echo "$1";; esac; }
while IFS= read -r m; do
  [ -n "$m" ] || continue
  pat=$(syn "$m")
  grep -qE "$pat" "$SKILL" || fail "module '$m' never referenced in SKILL.md (orphan module)"
done <<EOF
$MODS
EOF

exit $FAIL
