---
description: "regenloop living knowledge package — always-loaded index; load cards on demand via Read tool"
---

Cards are at `regenloop/knowledge/cards/<slug>.md` where slug = the Card column value.
When a task touches a specific module, load its card using the Read tool:
  Read("regenloop/knowledge/cards/<slug>.md")  ← resolve against repo root

<!-- regenloop-knowledge-index-start -->
| Card | Anchor | Last-verified |
|------|--------|---------------|
| accelerate-tests | skills/accelerate-tests | 2026-08-02 |
| agents | agents | 2026-08-20 |
| architect-orchestrator-ref | architect-orchestrator-ref | 2026-07-22 |
| claudedocs | claudedocs | 2026-08-29 |
| cockpit | scripts/cockpit | 2026-08-28 |
| commands | commands | 2026-08-29 |
| docs | docs | 2026-08-29 |
| e2e-testing | e2e | 2026-08-24 |
| hooks | hooks | 2026-08-29 |
| scripts | scripts | 2026-08-29 |
| templates | templates | 2026-08-21 |
| tests | tests | 2026-08-29 |
| architect | skills/architect | 2026-08-19 |
| context-diet | skills/context-diet | 2026-07-22 |
| frontend-design | skills/frontend-design | 2026-07-22 |
| gate-runner | skills/gate-runner | 2026-08-19 |
| green-gate | skills/green-gate | 2026-08-19 |
| regenloop-bisect | skills/regenloop-bisect | 2026-07-22 |
| regenloop-doctor | skills/regenloop-doctor | 2026-08-24 |
| regenloop-fix | skills/regenloop-fix | 2026-08-02 |
| regenloop-init | skills/regenloop-init | 2026-08-07 |
| regenloop-migrate | skills/regenloop-migrate | 2026-07-22 |
| regenloop-report | skills/regenloop-report | 2026-07-22 |
| regenloop-run | skills/regenloop-run | 2026-08-29 |
| regenloop-ship | skills/regenloop-ship | 2026-08-29 |
| regression | skills/regression | 2026-08-19 |
| tdd | skills/tdd | 2026-08-19 |
| verification-before-completion | skills/verification-before-completion | 2026-07-22 |
| writing-skills | skills/writing-skills | 2026-07-22 |
<!-- regenloop-knowledge-index-end -->
For build/test/run commands and overall structure: invoke regenloop_init via the plugin's resolved path with `map --show`
