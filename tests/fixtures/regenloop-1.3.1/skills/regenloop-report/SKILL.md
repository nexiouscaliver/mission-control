---
name: regenloop-report
description: Run after any regenloop run, or whenever you want to see how effective the loop has been, to read the regenloop ledger and present the catch-rate summary — total runs, caught rate, new failures caught, and breakdown by source (green-gate, pre-push, regression).
---

# Loop Report

Reads the run ledger appended by regenloop skills and hooks; presents catch-rate stats.

## Procedure

1. **Locate ledger.py** using the standard plugin path pattern:
   ```bash
   LEDGER="${CLAUDE_PLUGIN_ROOT:-}/scripts/ledger.py"
   [ -f "$LEDGER" ] || LEDGER=$(find "$HOME/.claude/plugins" -name ledger.py -path '*regenloop*' 2>/dev/null | head -1)
   [ -f "$LEDGER" ] || { echo "ledger.py not found — is regenloop installed?"; exit 1; }
   ```

2. **Run the report** from the target repo's working directory:
   - All time: `python3 "$LEDGER" report`
   - Scoped window: `python3 "$LEDGER" report --since <days>`

3. **Present the summary** to the user:
   - Total runs in the window.
   - Catch rate (caught / total, as a percentage).
   - Total new failures caught before CI.
   - Breakdown by source (green-gate, pre-push, regression, etc.).

4. **If the ledger is empty or missing**, say so — this is expected before the first run.

## Guardrails

- Do NOT load the ledger file into the LLM context; always use `ledger.py report` to summarise it.
- Do NOT modify the ledger. This command is read-only.
