#!/usr/bin/env bash
# Case 03 — frontmatter allowed-tools covers every tool the SKILL text mandates
# (A1 F8 fix; council repair R3: delete_note added — close mode's §6 deletion needs it).
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$ROOT" || exit 1
SKILL="plugins/mission-control/skills/mission-control/SKILL.md"
FAIL=0
fail() { echo "  03: $*" >&2; FAIL=1; }

FM_END=$(awk 'NR==1&&$0!="---"{exit} NR>1&&$0=="---"{print NR;exit}' "$SKILL")
FM=$(head -n "$FM_END" "$SKILL"); BODY=$(tail -n +"$((FM_END+1))" "$SKILL")
ALLOW=$(printf '%s\n' "$FM" | awk '/^allowed-tools:/{sub(/^allowed-tools: *\[?/,"");sub(/\]$/,"");gsub(/,/," ");print}')

has() { case " $ALLOW " in *" $1 "*) return 0;; *) return 1;; esac; }

# 1. Auto layer: literal tool tokens used in the body must be granted.
for T in Read Write Edit Glob Grep Bash Agent WebFetch; do
  if printf '%s' "$BODY" | grep -qw "$T"; then has "$T" || fail "body uses $T but allowed-tools omits it"; fi
done
for M in $(printf '%s' "$BODY" | grep -oE 'mcp__[a-z0-9_-]*__[a-z0-9_]*' | sort -u); do
  has "$M" || fail "body names $M but allowed-tools omits it"
done

# 2. Curated floor: the state model's MCP family + delete_note (prose-named, invisible to grep).
while IFS= read -r R; do
  [ -n "$R" ] || continue
  has "$R" || fail "required tool $R (tests/required-tools.txt) missing from allowed-tools"
done < tests/required-tools.txt

# 3. Advisory inverse: granted-but-unused entries.
for A in $ALLOW; do
  printf '%s' "$BODY" | grep -qw "${A##*__}" || echo "  03: WARN allowed-tools entry $A never named in body (stale?)"
done

exit $FAIL
