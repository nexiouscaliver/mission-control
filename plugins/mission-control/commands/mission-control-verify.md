---
description: "Mission Control — verify: evidence-verify a finished session (accepts a bare MR/PR number)"
argument-hint: <session | MR/PR reference, e.g. !1560 or #42>
---

Execute the **mission-control** skill in **verify** mode. First read `skills/mission-control/SKILL.md` — it is authoritative; this command is only an entry point.

Operator input: $ARGUMENTS — a session reference, a bare MR number (`!1560`), or a PR number (`#42`); resolve it to the prompt-log row via the program note before verifying.

Follow §4 (verify) exactly: the handoff is claims, not evidence — independently re-check reality (merge ancestry, API state, pushed branch at the claimed head, suite re-run under the configured cap where practical, artifact re-reads, box state via the project facts note's box entry, session-debris checks), record the verdict per prompt-log row with pasted evidence lines, and forge a remediation prompt on the spot if gaps exist. Load `references/lessons.md` first.
