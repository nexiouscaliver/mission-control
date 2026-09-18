---
id: regenloop-ship
anchor: skills/regenloop-ship
anchor_hash: aeb4c1926e96b179cae981b56edfa6514294c1f1
last_verified: 2026-08-29
---
## Why this module exists

Finished branch → DRAFT MR → review→triage→fix→re-gate to Ready — **never merges, never approves** (`mr_merge_guard.py`). Agents: [[agents]]; GitLab via `mr_notes.py`.

## Key invariants

- **Every stage posts to the MR** (fix replies carry the SHA).
- **Ready ⇔ gates green AND CI `ok`/`none` AND merge-check not `DO_NOT_MERGE`** (→ Draft); red CI is no fix target.
- **mr-triage `VALID` alone authorises unattended fixes** (sub-70 never VALID); `UNRESOLVED` escalates once, rides as a note; only red/`could_not_run` gates keep Draft.
- **mr-merge-check runs after Ready**: re-derives from final state + human threads; SAFE_TO_MERGE/MERGE_WITH_CAUTION/DO_NOT_MERGE.
- **Never edits `gates.toml`/`never_touch`**. `--no-verify` only under recorded operator approval AND disclosed on MR.
- **Gate base is an input**: `SHIP_BASE` or the run→ship record (`regenloop_state.py`: base_sha, launch_branch, ship_tier; exit 3 = none). `merge-base origin/HEAD` is wrong.
- **Push/create non-interactive**: `GIT_TERMINAL_PROMPT=0 git push -u "$REMOTE" HEAD`; `glab mr create --fill --yes --draft`; IID/BRANCH/$REMOTE re-derived per block.
- **`mr_notes.py`/`regenloop_state.py` resolve `--plugin-only`; `gate_runner.py` doesn't**.
- **§5e persists the round base pre-commit; §5f reads it by exit code** (never `HEAD~1`; `no_evidence` → §5g).
- **Review worktree refreshed each round ≥2** (fetch + `reset --hard`, `.worktrees/`).
- **Secrets scrubbed**; mr-security-reviewer = sole pre-push secret scan (always).
- Fix commits round-scoped; `MAX_ROUNDS = 3`.
- **New**: Step 0 any non-default branch; §5h merge-never-rebase bound; §5f mutation-proof new tests; §5g conditional card verification; §2 detached long-ops; §9 movement-aware merge-check.
- Pinned by 8 new `test_regenloop_ship_surface.py` classes (~20 total).

## Gotchas

- Requires `glab` + GitLab remote (no GitHub path).
- Lint gate = pinned `uvx ruff@0.16.2 check {changed}` (file-scoped — wider sets red-gate innocent).
