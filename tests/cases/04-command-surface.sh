#!/usr/bin/env bash
# Case 04 — command-surface consistency (the 18-minute 1.1.0->1.2.0 rename specimen, mechanized).
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$ROOT" || exit 1
SKILL="plugins/mission-control/skills/mission-control/SKILL.md"
FAIL=0
fail() { echo "  04: $*" >&2; FAIL=1; }
MODES="plan prompts verify next close"

for M in $MODES; do
  F="plugins/mission-control/commands/mission-control-${M}.md"
  [ -f "$F" ] || { fail "$F missing"; continue; }
  FM_END=$(awk 'NR==1&&$0!="---"{exit} NR>1&&$0=="---"{print NR;exit}' "$F")
  [ -n "$FM_END" ] || { fail "$F: broken frontmatter"; continue; }
  FM=$(head -n "$FM_END" "$F")
  echo "$FM" | grep -q '^description:' || fail "$F: missing description"
  if echo "$FM" | grep -q '^argument-hint:'; then
    V=$(echo "$FM" | sed -n 's/^argument-hint: *//p')
    [ -n "$V" ] || fail "$F: argument-hint present but EMPTY (the next.md specimen)"
  else
    fail "$F: no argument-hint key"
  fi
  grep -q 'SKILL.md' "$F" || fail "$F: does not reference SKILL.md"
  grep -qE '§[0-9]' "$F" || fail "$F: no §-mode reference into SKILL.md"
done

# SKILL frontmatter argument-hint lists exactly the five modes (token-set compare).
FM_END=$(awk 'NR==1&&$0!="---"{exit} NR>1&&$0=="---"{print NR;exit}' "$SKILL")
HINT=$(head -n "$FM_END" "$SKILL" | sed -n 's/^argument-hint: *//p' | grep -oE '<[^>]+>' | head -1 | tr -d '<>')
GOT=$(printf '%s' "$HINT" | tr '|' ' ' | xargs -n1 | sort -u | xargs)
WANT=$(echo $MODES | xargs -n1 | sort -u | xargs)
[ "$GOT" = "$WANT" ] || fail "SKILL argument-hint mode set ('$GOT') != installed commands ('$WANT')"

exit $FAIL
