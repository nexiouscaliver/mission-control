#!/usr/bin/env bash
# run_smoke.sh — mission-control skill smoke tests (T5 design; runner runs every cases/*.sh).
# Red at the v1.2.0 base by design: the cases assert v1.3.0 invariants.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT" || exit 1
FAIL=0
for c in tests/cases/*.sh; do
  if bash "$c"; then echo "PASS ${c#tests/cases/}"; else echo "FAIL ${c#tests/cases/}"; FAIL=1; fi
done
exit $FAIL
