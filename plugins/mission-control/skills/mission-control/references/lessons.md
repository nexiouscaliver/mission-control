# Lessons — the failure modes behind the rules

Each rule in the skill exists because a real session paid for it. Provenance: the omniforge program, 2026-09-06 → 2026-09-15 (5 waves, ~15 sessions, 3 canaries, 2 production aborts that worked).

1. **The stale-base trap.** A repo's main checkout sat *behind the deployed engine*; sessions forking from "whatever is checked out" would have silently invalidated themselves. → Base verification immediately after kickoff, mismatch = cleanup + STOP, and per-repo base rules in the project facts note (detached origin/main; never the stale local copy).

2. **Handoff capsules overclaim.** Two "COMPLETE" sessions needed reality checks: one had a 3-commit branch missing its feature commit's equivalent (content lived on a stray branch from a race), another claimed "shipped" with the branch never pushed. → Verify never trusts the capsule: ancestry checks, API state, suite re-runs, artifact re-reads, stray-branch and worktree debris checks.

3. **Unattended sessions stall on questions.** The Clarify gate and the active-goal disambiguation hook can both wait forever for a human. → Prompts pre-answer the Clarify batch and record it as assumptions; never run two fresh goals in one repo overnight.

4. **The identity guard fights cleverness.** `-c` overrides and `git config` rewrites are blocked; so is working around them. → Plain git only, in the prompt; credential fixes go through config-*file* edits (allowed) applied by the controller, never through flagged commands in the session.

5. **Two sessions raced one checkout.** Two parallel lanes in the same repo interleaved commits onto one branch name; both had to be rebuilt in dedicated worktrees. → One session per repo per lane, per-session worktrees mandatory, slug-prefixed fixtures, and verify-mode checks for stray branches every time.

6. **regenloop-run needs an onboarded repo.** A repo without `gates.toml`/orchestrator state produces at best a degraded run and at worst a vacuous-gates stall. → Lanes in non-onboarded repos run as plain deep sessions with the discipline written into the prompt; onboarding is tracked as its own work item.

7. **Kill-switch-first aborts are the cheapest bug-finder.** A trust-boundary mismatch and a fetch-race silent-skip were both caught in production by pre-committed abort rules inside forged prompts ("any anomaly → kill switch → STOP → report") and became cheap next-wave fixes. → The abort module attaches to every production-adjacent prompt.

8. **Silent skips are the enemy.** A sweep mislabeled an unresolvable head as `SKIP_EMPTY_DELTA` and silently did nothing — the exact class of invisible failure the whole method exists to prevent. → Prompts touching state machines demand fail-loud behavior and stated reasons; skips are legal only with both endpoints resolvable.

9. **Fairness and visibility in throttling.** A per-project daily cap starved an unrelated MR (!1517) with only a journal line to show for it. → Caps moved per-MR; every refusal posts a visible notice with the recovery action (`/push-check force`). When forging anything that refuses work, forge the refusal's visibility and its escape hatch too.

10. **Build in parallel, enable in sequence.** Construction across lanes overlapped freely; nothing went live before what it depended on was live (the corrected verdict model shipped before the sweep was switched on). → The plan's enablement sequence is load-bearing; `next` enforces it.

11. **Bumps ride feature branches.** The never-merges guard makes direct default-branch commits impossible; the proven pattern is the version-bump as the final isolated commit of the feature branch, so the human merge carries it. → The release-prep module.

12. **Evidence lines, not summaries.** A gate verdict once lived only in tool output and was rejected as unproven. → Every verification and every session's final report paste the decisive lines.

13. **OOM history makes parallelism a proposal.** A host OOM during parallel scans forced a single-thread mandate. → Headroom is measured before forging; caps derive from it; the operator approves parallelism.

14. **Program state belongs in the vault, not the repo tree.** Working docs in `claudedocs/` leaked into the git tree and needed manual sweeps. → Vault-native state: one program note while active, distilled knowledge by project at close, working docs deleted.

15. **Prompts are delivered by paste.** The prompt files were bookkeeping, never the delivery mechanism — the operator always pasted from chat. → No prompt files anywhere; the program note's log row is the record.

16. **Ask only genuine forks.** A single 4-question round (with recommendations marked) reshaped a major design; everything else had defaults. → plan's question discipline.
