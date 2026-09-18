#!/usr/bin/env bash
# Case 05 — version identity, unit form (plugin.json == SKILL frontmatter == CHANGELOG head
# version entry; tag advisory pre-release per council repair R5 — the tag is cut at release).
set -u
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$ROOT" || exit 1
SKILL="plugins/mission-control/skills/mission-control/SKILL.md"
PJ="plugins/mission-control/.claude-plugin/plugin.json"
FAIL=0
fail() { echo "  05: $*" >&2; FAIL=1; }

V=$(jq -r '.version // empty' "$PJ")
[ -n "$V" ] || { fail "plugin.json has no version"; exit 1; }

FM_END=$(awk 'NR==1&&$0!="---"{exit} NR>1&&$0=="---"{print NR;exit}' "$SKILL")
SV=$(head -n "$FM_END" "$SKILL" | awk '/^version:/{sub(/^version: *"?/,"");sub(/"$/,"");print}')
[ -n "$SV" ] || fail "SKILL.md frontmatter missing version (required since 1.3.0)"
[ "$SV" = "$V" ] || fail "SKILL frontmatter version ($SV) != plugin.json ($V)"

CLOG=$(awk '/^## \[/{if ($0 ~ /Unreleased/) next; sub(/^## \[/,"");sub(/\].*/,"");print;exit}' CHANGELOG.md)
[ "$CLOG" = "$V" ] || fail "CHANGELOG head version entry ($CLOG) != plugin.json ($V)"

[ -n "$(git tag -l "v$V")" ] || echo "  05: WARN tag v$V not cut yet (cut at release — advisory, R5)"

exit $FAIL
