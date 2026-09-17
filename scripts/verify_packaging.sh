#!/usr/bin/env bash
# verify_packaging.sh — mission-control release gate (J2 P2-2, pulled into 1.3.0 per J2 risk #2;
# council repair R5 applied: the tag check is advisory pre-tag, blocking on mismatch).
# Blocking failures exit 1; ADVISORY lines warn only (install surfaces + the not-yet-cut tag
# refresh after the release by definition — a blocking check there would deadlock the release).
# Usage: bash scripts/verify_packaging.sh   (from anywhere; resolves repo root itself)
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT" || exit 1
PDIR="plugins/mission-control"; PJ="$PDIR/.claude-plugin/plugin.json"
SKILL="$PDIR/skills/mission-control/SKILL.md"
FAILS=0
fail() { echo "FAIL [blocking] $*" >&2; FAILS=$((FAILS+1)); }
warn() { echo "WARN [advisory] $*" >&2; }

# 1. Both manifests parse as JSON.
jq -e . "$PJ" >/dev/null 2>&1 || fail "$PJ does not parse as JSON"
jq -e . marketplace.json >/dev/null 2>&1 || fail "marketplace.json does not parse as JSON"

# 2. Marketplace entry resolves to the plugin that actually exists.
SRC=$(jq -r '.plugins[0].source // empty' marketplace.json 2>/dev/null)
{ [ "$SRC" = "./plugins/mission-control" ] && [ -f "$PJ" ]; } \
  || fail "marketplace.json plugins[0].source ('$SRC') must resolve to $PJ"

# 3. Frontmatter delimiters + required keys: SKILL.md (name, description) and every command
#    file (description; commands carry no name field — filename is the name).
fm_end() { awk 'NR==1&&$0!="---"{exit} NR>1&&$0=="---"{print NR;exit}' "$1"; }
for f in "$SKILL" "$PDIR"/commands/*.md; do
  N=$(fm_end "$f"); [ -n "$N" ] || { fail "$f: frontmatter delimiters missing/broken"; continue; }
  FM=$(head -n "$N" "$f")
  echo "$FM" | grep -q '^description:' || fail "$f: frontmatter missing description"
  [ "$f" = "$SKILL" ] && { echo "$FM" | grep -q '^name:' || fail "$f: frontmatter missing name"; }
done

# 4. Every references/*.md the shipped text names exists (scans SKILL.md AND commands/*.md —
#    both load references).
for ref in $(grep -rho 'references/[A-Za-z0-9._-]*\.md' "$PDIR" | sort -u); do
  [ -f "$PDIR/skills/mission-control/$ref" ] \
    || fail "$ref is named in shipped text but missing from $PDIR/skills/mission-control/"
done

# 5. Version identity: plugin.json == SKILL frontmatter == CHANGELOG head version entry.
#    ([Unreleased] may sit at the CHANGELOG head between releases — skipped by design.)
VER=$(jq -r '.version // empty' "$PJ" 2>/dev/null); [ -n "$VER" ] || fail "$PJ: no version"
SVER=$(head -n "$(fm_end "$SKILL")" "$SKILL" | awk '/^version:/{sub(/^version: *"?/,"");sub(/"$/,"");print}')
[ -n "$SVER" ] || fail "SKILL.md frontmatter missing version (required since 1.3.0, C2-3)"
[ "$SVER" = "$VER" ] || fail "SKILL.md frontmatter version ('$SVER') != plugin.json ($VER)"
CLOG=$(awk '/^## \[/{if ($0 ~ /Unreleased/) next; sub(/^## \[/,"");sub(/\].*/,"");print;exit}' CHANGELOG.md)
[ "$CLOG" = "$VER" ] || fail "CHANGELOG head version entry ($CLOG) != plugin.json ($VER)"

# 5b. Tag identity (R5 split): advisory while v$VER is not yet cut; blocking on a mismatch.
TAG=$(git tag -l | sed 's/^v//' | sort -n -t . -k1,1 -k2,2 -k3,3 | tail -1)
if [ -n "$(git tag -l "v$VER")" ] && [ "$TAG" != "$VER" ]; then
  fail "tag v$VER exists but latest tag is v$TAG — version identity broken"
elif [ -z "$(git tag -l "v$VER")" ]; then
  warn "tag v$VER not cut yet — cut before publishing (current latest: ${TAG:-none})"
fi

# 6. ADVISORY — marketplace synced copy drift.
MKT="$HOME/.zcode/cli/plugins/marketplaces/mission-control"
if [ -d "$MKT" ]; then
  diff -rq --exclude=.git --exclude=evaluation --exclude=evaluation-iter2 "$ROOT" "$MKT" >/dev/null 2>&1 \
    || warn "marketplace synced copy ($MKT) differs from repo — refresh (checklist step 7)"
else
  warn "no marketplace synced copy at $MKT (skip on non-maintainer hosts)"
fi

# 7. ADVISORY — plugin cache: version dir present, no stale dirs, content identical.
CACHE="$HOME/.zcode/cli/plugins/cache/mission-control/mission-control"
if [ -d "$CACHE" ]; then
  [ -d "$CACHE/$VER" ] || warn "cache lacks a $VER dir — refresh after release (C2-3 dangling-registration risk)"
  STALE=$(ls "$CACHE" | grep -vx "$VER" || true)
  [ -z "$STALE" ] || warn "stale cache dirs: $STALE — expect dangling registrations until harness restart"
  [ -d "$CACHE/$VER" ] && ! diff -rq "$PDIR" "$CACHE/$VER" >/dev/null 2>&1 \
    && warn "cache $CACHE/$VER differs from repo $PDIR"
fi

echo "---"
if [ "$FAILS" -eq 0 ]; then echo "OK: packaging blocking checks passed (version $VER)"; exit 0
else echo "REFUSING: $FAILS blocking check(s) failed — do not tag"; exit 1; fi
