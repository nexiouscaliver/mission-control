---
description: "Mission Control — next: human actions owed, lanes free, the next prompts"
argument-hint: [program-slug]
---

Execute the **mission-control** skill in **next** mode. First read `skills/mission-control/SKILL.md` — it is authoritative; this command is only an entry point.

Operator input: $ARGUMENTS (optional program slug — omitted with several active programs: list them and ask; omitted with one: proceed)

Follow §5 (next) exactly: read the program note (resolved per SKILL §1 when several are active), report human actions owed (with links) → lanes free → waves unblocked; emit the next prompts via §3 or state plainly what blocks. Handles "Part 2 only" re-invocations for two-part packages whose Part 1 merged, and enforces the enablement sequence — refuse to forge around a dependency that is not live. Before emitting any prompt, load `references/prompt-anatomy.md`, `references/lessons.md`, and `references/regenloop-interface.md`.
