# Verify runbook — the ordered commands of SKILL §4

Procedure is SKILL §4; these are its commands. 15 ordered commands after the two setup lines; `<fill-in>` items are per-lane values — `R` (the lane repo root) and `S` (the slug) are set per program, session ids and the disputed line per verify. Every static path below was verified live on 2026-09-17 against the regenloop 1.3.1 plugin and a real lane repo's `regenloop/local/` layout. Command 15 is deliberately last and conditional.

```bash
R=<lane-repo-root>; ORCH=$R/regenloop/local/orchestrator; S=det-scan-linux-pins
RL=~/.zcode/cli/plugins/cache/regenloop/regenloop/1.3.1/scripts            # re-resolve version at plan (interface doc §10)
grep "| $S |" $ORCH/_archive/INDEX.md; ls $ORCH/goals/ | grep -x $S || echo "goal terminal"
cat $ORCH/_archive/$S/record.json 2>/dev/null || cat $ORCH/goals/$S/record.json
python3 $RL/regenloop_state.py get $S base_sha --root $ORCH; echo "exit=$? (0=exists 3=absent 1=unreadable)"
python3 -c "import json;r=json.load(open('$R/regenloop/local/regression/goals/$S/last-full-report.json'));print('full:',r['head_sha'],r['base'],r['verdict'])"
python3 -c "import json;r=json.load(open('$R/regenloop/local/green-gate/goals/$S/last-report.json'));print('fast:',r['head_sha'],r['base'],r['verdict'])" 2>/dev/null || echo "no fast report"
tail -4 $ORCH/_archive/$S/queue.md 2>/dev/null || tail -4 $ORCH/goals/$S/queue.md
B=$(python3 -c "import json;print(json.load(open('$ORCH/_archive/$S/record.json'))['base_sha'])"); grep -c "$B" $R/regenloop/local/ledger.jsonl
cat $ORCH/goals/$S/budget.json 2>/dev/null || cat $ORCH/_archive/$S/budget.json
python3 $RL/regenloop_state.py status --root $ORCH
git -C $R fetch -q && git -C $R merge-base --is-ancestor $B origin/main && echo ANCESTOR-OK || echo NON-ANCESTOR-ALARM
git -C $R log --oneline $B..origin/main | head -8; git -C $R diff --name-only $B..origin/main | head -8
DB=~/.zcode/cli/db/db.sqlite; sqlite3 $DB "SELECT id,parent_id,substr(title,1,60) FROM session WHERE id='<master-sess-id>' OR parent_id='<master-sess-id>';"
grep -n "<disputed-line>" ~/.zcode/cli/rollout/model-io-<family-session-id>.jsonl | head -5   # transcript tiebreaker; no file ≠ no session
git -C $R worktree list | head; git -C $R branch -a | grep "loop/" | head; git -C $R status -sb | head -3
cd $R && python3 -m unittest discover -s tests 2>&1 | grep -E "^(Ran |OK|FAILED|passed|failed)" | tail -3   # ONLY merged trees or report mismatch
```

Line 15's suite command is the lane repo's own gates.toml test invocation — read it from the repo, never from this example (the omniforge specimen runs `python3 -m unittest discover -s tests`; use the repo's venv where one exists).
