---
description: "Mission Control — verify: evidence-verify a finished session (accepts a bare MR/PR number)"
argument-hint: <session | MR/PR reference, e.g. !1560 or #42>
---

Execute the **mission-control** skill in **verify** mode. First read `skills/mission-control/SKILL.md` — it is authoritative; this command is only an entry point.

Operator input: $ARGUMENTS — a session reference, a bare MR number (`!1560`), or a PR number (`#42`); resolve it to the prompt-log row via the program note before verifying.

Follow §4 (verify) exactly: the handoff is claims, not evidence — read the run's own machine-written records first (archive INDEX row, `record.json`, per-slug gate reports, queue/ledger/budget), then the external re-checks (merge ancestry, API state, pushed branch at the claimed head, artifact re-reads, box state via the project facts note's box entry, session-debris checks; a hand suite re-run only for merged trees or report mismatches), record the verdict per prompt-log row with pasted evidence lines, and forge a remediation prompt on the spot if gaps exist. Load `references/lessons.md` and `references/regenloop-interface.md` (§9 verify-side knowledge; commands: `references/verify-runbook.md`) before verifying.
