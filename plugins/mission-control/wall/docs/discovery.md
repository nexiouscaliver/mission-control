# Boot-time program-note discovery

Reference for the discovery pass that runs once per boot
(`mc_wall/tower/discovery.py`, wired in `mc_wall/server/tower_boot.py`):
after the declared programs/repos are read from wall.json, undeclared vault
notes named `mission-control-<slug>-program.md` are appended to the program
list — plus repos derived from their lane rows. Declared wall.json config
is always authority. Discovery is boot-only: the set is fixed until restart
(below); note content and lane status refresh on the normal poll.

## Candidate filenames

A file is a candidate iff its name matches
`mission-control-<slug>-program.md` with `<slug>` = `[a-z0-9]+(-[a-z0-9]+)*`
(kebab-case, lowercase, case-sensitive). Anything else — wrong case, empty
slug, different suffix, a directory named like a note — is ignored silently.

## The objective line

A candidate must parse (`mc_wall/tower/notes.py` `parse_note`) with both a
recognized prompt-log table and a non-empty objective. The objective is
read from the first line of the note whose trimmed, lowercased form begins
with the string `objective` — the colon is NOT part of the match:
`Objective:`, `objective:`, `OBJECTIVE:` all match. The value is the text
after the first `:`, trimmed; a matching line with no colon yields an empty
objective. `**Objective:**` does NOT match (the trimmed line begins `**`,
not `objective`), and neither does an indented `- Objective:`.

Checks run in order read → table → objective; the first failure rejects the
candidate with one degraded line, `<path>` being the candidate's path:

- undecodable bytes → `discovery degraded: unreadable note <path>`
- no recognized table → `discovery degraded: no prompt-log table in <path>`
- empty objective → `discovery degraded: no objective in <path>`

Discovery degraded lines surface in the wall's degraded list, sorted after
all runtime entries.

## Search dirs

Scanned: the distinct parent dirs of the declared programs' `note_glob`
paths, each `~`-expanded and otherwise verbatim (a bare `*.md` glob
contributes nothing). Listing uses `os.scandir` on the literal path, so
glob metacharacters in a dirname are never re-matched. With no declared
programs — or when every dirname is empty — the single fallback
`~/work/memory-vault/shared/programs` is scanned. A missing or unreadable
search dir is not an error: nothing found, nothing degraded (a heuristic
scan, not a declaration).

## Precedence and duplicates

A discovered note whose slug equals a declared program name is skipped
silently — wall.json wins. Two discovered notes with the same slug: the
first in scan order (sorted dirs, then sorted filenames) wins; each later
one is skipped with
`discovery degraded: duplicate program <slug> at <path>`.

## The set is fixed at boot

Programs and repos resolve once, at boot. Restart the wall to pick up NEW
program notes or note deletions:

    launchctl kickstart -k gui/$(id -u)/ai.zcode.mc-wall

Edits to an already-discovered note's content or lane statuses need no
restart — they refresh on the normal ~5 s poll.

## Derived repos

For each discovered program, the distinct repo path tokens across its
prompt-log rows (the first `~/…` or `/…` whitespace token of the
repo/branch cell) become `repos[]` entries: `name` = the path basename,
`path` = the `~`-expanded path, `host` = `github` | `gitlab` | `other`,
inferred from the `git -C <path> remote -v` fetch URLs (substring match;
no match → `other`). Each distinct path is probed once — a repeated token,
even a failed one, is not re-probed. A repo is skipped with a degraded
line naming the path when it is absent or not a directory
(`discovery degraded: repo <path> not a directory`), has an empty basename
(`… has no name`), its name is already taken at a different path
(`… repo name <name> at <path> conflicts with <other path>`), git fails
(`… git remote failed`), or no `(fetch)` URL is found
(`… has no git remote`); the same name at the same path dedupes silently.
Derived repos are appended after the declared ones — declared `repos[]`
entries are never duplicated or modified.
