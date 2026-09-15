# Prompt anatomy — the forge grammar

Every deep prompt is assembled from an **invariant skeleton** + **fresh-recon variables** + **situational modules**. The skeleton is never skipped; modules are selected by the package's nature; variables are always verified the same turn they are written.

## The skeleton (always, in this order)

1. **Invocation + goal.** For regenloop lanes: `/regenloop-run --deep --safe --ship <goal in one sentence>. Suggested slug: <slug>.` For plain-deep lanes (repos not regenloop-onboarded): "You are an implementation agent…" with the same discipline hand-written.
2. **Context — evidence lines pasted verbatim.** Journal output, API results, the incident lines that motivated the work. Never summaries: proof that exists only in the controller's scrollback is invisible.
3. **Unattended rules (or attended rules).** Never block on a question; the Clarify batch is pre-answered in the prompt and recorded by the session as `decision:`/`assumption:` entries (autonomy=unattended; ship=yes via --ship; tests capped at N; scope = below only). Attended variant: explicit evidence checkpoints instead of a time bound.
4. **Base verification, immediately after kickoff.** The exact SHA; "read the recorded base_sha back and compare; ANY mismatch → run cleanup and STOP; never silently self-correct the base." Include the repo-specific base rule from the project facts note (e.g. detached origin/main, never the stale local copy).
5. **READ FIRST — nothing else.** The program note permalink (vault), the package's own brief section, the design sections it cites. Loading anything more wastes the session's context.
6. **Scope in / scope out + owned files.** What the session must not touch — including the other lanes' repos. Owned-files lists make cross-session conflict checks mechanical.
7. **Engineering rules.** TDD two-commit receipts (red, then green) with a fixture prefix unique to this session; the repo's exact test command + worker cap from the facts note; count-gate sync where one exists; plain `git commit -m` with the default identity, no `-c` flags, no attribution; evidence lines into the MR/PR body.
8. **Stall rule.** N consecutive gate-red retry rounds on one task, or past the time bound → bring the current task to a cleanly-reported state, run the full cleanup protocol, STOP. Never thrash, never improvise around a guard refusal.
9. **Pause points** (human gates): "when Ready and green, PAUSE — surface the MR for human merge. STOP cleanly if the operator is absent."
10. **Kill switches** (if production-adjacent): named, one line, "kill switch first, always."
11. **Records.** Window note or program-note append with pasted decisive lines; what the next session in the lane needs to know.

## Situational modules

| Module | Attach when | Contents |
|---|---|---|
| **Part 1 / Part 2** | Anything crossing a merge or deploy boundary | Part 1 = code + ship + merge-pause. Part 2 = patch/enable/verify, re-invocable as "Part 2 only"; its first lines verify the merge landed (ancestry check) |
| **Deploy window** | Part 2 touches a box/service | Pin (fresh pinned worktree — never the ambient checkout) → backup (timestamped) → swap → verify (sha/size N/N + 0 extras) → checkpoints before restarts → restart → healthz + clean journal → resume |
| **Dogfood / canary** | Behavior must be proven live | Scratch MR, closed-unmerged; expected artifacts named with note ids; latency expectations (a 1-second "sweep" means it skipped — investigate); skip-path and dedup checks; "never merge" |
| **Abort path** | Anything that can misbehave in production | Pre-committed: "any anomaly → <kill switch> → restart → STOP → full report." Two real production bugs were caught cheaply exactly this way |
| **Release prep** | A version bump must ship | Bump commit appended to the feature branch as the final isolated commit (the never-merges guard makes direct-main commits impossible anyway); release notes drafted; everything-but-the-click prepared for the operator |
| **Remediation** | verify found gaps | Gap-scoped only; opens with "what was claimed vs what verify found"; same skeleton; explicitly bounds the session away from refactoring green work |
| **One-shot** | Triage says single session | The light form: setup verification (HEAD == expected SHA else STOP) → branch name → 3–6 line scope → test cap → plain-commit rule → ship step → stall rule. No program machinery |

## The red-team gate (before emitting any prompt)

1. Every SHA / flag / path / knob name exists **right now** (verified this turn)?
2. Any owned-file collision with an in-flight session?
3. Anything human-only disguised as agent work?
4. Does the stall rule fire cleanly on the worst plausible failure?
5. Does Part 2's precondition match what Part 1 actually produces?
6. Do the RAM-derived caps fit the machine as it stands?

A prompt that fails any check is reforged. The operator should never be the one to find a stale SHA.

## Goal statements

Every prompt ships with a one-line goal statement the controller prints above it — the essence, so the operator can sanity-check intent before pasting: *"Make throttling fair, visible, and overridable: … — deployed and proven live."*
