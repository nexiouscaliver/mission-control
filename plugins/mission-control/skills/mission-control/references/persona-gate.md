# Persona gate — the operator-fit reviewer

A one-agent advisory review that puts an **operator mirror** in front of an operator-facing artifact before the operator sees it. The persona is constructed from recorded facts, runs for at most two rounds, and its output is ADVISORY everywhere — it gates operator **review**, never build. Wired into two places and banned from one: `plan` may pre-answer an operator-facing fork with a persona ruling offered as the marked recommendation (SKILL §2 step 5), and the red-team gate's operator-fit check runs it on operator-facing forges (anatomy check 11); `verify` never runs it — §4 is evidence-only, and persona testimony is not evidence.

Why it exists (2026-09-19 Wall design round, fork FX1): the system fleet and the persona reviewer catch **disjoint finding classes**. The fleet caught correctness and ops (verify deep-link birthing a new session; the launchd PATH trap; hash-only matching brittleness) while the persona caught operator-fit (the goal-paste race as the #1 pain; dead-session wallpaper; GitLab !N vs GitHub #N confusion; "one lying chip and I stop trusting all"; NEEDS ME NOW; the 9pm-confusion list) and made the launch-mode ruling ("machinery beats discipline"). Yield: ~10 material fit-findings + one load-bearing ruling for ~100k tokens, zero operator minutes.

## §1 The grounded brief — construction

The persona is never written freehand. Before any round, write the brief, and every line of it is either **sourced** or **labeled**:

- **Sourced lines cite their origin**: the project facts note, the program note (objective, operator directives, recorded decisions), and vault user-notes about the operator — working style, daily mechanics, known pains, stated preferences. A trait that is in none of those is not a fact about the operator.
- **Persona-stated specifics are labeled assumptions.** Anything the brief attributes to the operator beyond a sourced line is written `assumption:` — the persona is a mirror built from records plus marked hypotheses, not an oracle; the label is what keeps confabulation auditable.
- The brief names the artifact's **touchpoints with the operator's day** — where in the daily mechanics this artifact will actually be met (launch, glance, verify, the 9pm check) — because fit is judged at the touchpoints, not in the abstract.

## §2 The output contract — fixed

Every round emits exactly this, nothing else:

1. **Per-phase scores /10** — one score per phase or surface of the artifact (per panel, per flow step, per runbook section), each with a one-line justification.
2. **Ranked frictions** — the operator-pain list, most damaging first, each concrete: what hurts, when it hurts, and what it costs at the worst moment.
3. **The 9pm-confusion list** — everything a tired operator would misread: ambiguous labels, near-synonyms, ordering that presumes the design's rationale, anything that demands recall instead of recognition.
4. **Dealmakers / breakers** — what would make the operator keep using it vs. quietly stop.
5. **Fork rulings WITH rationale** — for each fork it was consulted on: the ruling, the why, marked hypothesis. A ruling without a rationale is discarded unread.
6. **Explicit verdict phrase** — exactly one of: `operator-fit: green (surface as designed)` | `operator-fit: green with frictions (surface; fix list attached)` | `operator-fit: red (rework before surfacing)`.

## §3 The two-round protocol

- **Round 1:** a fresh reviewer agent gets the grounded brief + the artifact and runs the full contract.
- **Round 2 RESUMES the same agent** — continuity is the mechanism: it re-checks its own round-1 complaints against the artifact (were they real, were they addressed, did round 1 under- or over-state), so fixes are verified by their own author rather than re-derived by a stranger who never made the complaint.
- **The round-2 dial is lazier and less forgiving:** assume the operator more tired, less patient, and less forgiving than round 1 assumed. This is the counterweight to agreeability inflation — a resumed reviewer tends to soften, the dial hardens, and the two cancel. Round 2's verdict supersedes round 1's and is the one recorded.
- **Hard cap: 2 rounds.** Diminishing returns are proven and correlated error grows with every round — the persona shares the controller's priors, so more rounds manufacture false confidence, not fit.

## §4 Epistemic rules — scope and limits

- **ADVISORY only.** Every persona output lands in the program note marked ADVISORY; it never overrides evidence, a machine report, or a verify verdict.
- **Gates operator review, never build.** "Persona satisfied" is the bar to surface an artifact to the operator — never a bar to merge, ship, or deploy. Build gates stay machine- and evidence-driven.
- **Operator veto always wins, and is recorded as the decision.** When the operator overrides a persona ruling, the override IS the decision; the program note records it together with the ruling it superseded.
- **Operator-facing artifacts only:** UIs, flows, runbooks, pasted prompts, daily-mechanics forks. **Never internal code** — code correctness has its own gates and the persona has no standing there.
- **The persona is a mirror, not the operator.** Its rulings are hypotheses, its specifics are assumptions (§1), and its errors correlate with the controller's own priors. It saves operator minutes exactly as long as nobody mistakes its verdicts for the operator's.
