"""Seed an UNMATCHABLE pending.json into <home>/state/ BEFORE boot.

Run only while the server is STOPPED (the store loads once at boot; seeding a
running server is a silent no-op). Unmatchable by construction: random
lane_tag / row_id / prompt_sha256, repo_root INSIDE the temp home. Clipboard
clobber is provably impossible: the matcher's realpath gate must resolve the
record's repo_root to a real session workspace, and the seed's repo_root
lives in the temp home. The ONLY realistic osascript residual is the
conflict/flag path, triggerable solely by quoting the seeded lane_tag into
session-visible text (subagent prompts, log lines, findings drafts) — never
quote the seeded lane_tag or record contents anywhere; reference the seed
only as its status + path. The 120 s no-birth advisory surfaces in the state
doc as an advisory line, with no clipboard effect.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import secrets
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import e2e_common  # noqa: E402


def build_record(status: str, home: pathlib.Path) -> dict:
    """Schema-accurate PendingRecord dict (mc_wall/server/pending.py:30-46)."""
    suffix = secrets.token_hex(4)
    now_ms = int(time.time() * 1000)
    return {
        "status": status,
        "flag": "ambiguous" if status == "flagged" else None,
        "reason": "e2e-seed" if status == "flagged" else None,
        "row_id": "e2e-seed-row-%s" % suffix,
        "lane_tag": "[e2e-seed-%s]" % suffix,
        "repo_root": str(pathlib.Path(home) / "seed-repo"),
        "prompt_sha256": secrets.token_hex(32),
        "launch_click_ms": now_ms,
        "matched_session_id": None,
        "matched_at_ms": None,
        "last_eval_ms": None,
        "advisory_120s_fired": False,
        "canary_fired": False,
        "updated_at_ms": now_ms,
        "version": 1,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="seed unmatchable pending.json")
    parser.add_argument("--home", required=True)
    parser.add_argument("--status", default="prompt-armed",
                        choices=list(e2e_common.SEED_STATUSES))
    args = parser.parse_args(argv)
    home = pathlib.Path(args.home).resolve()
    state = home / "state"
    try:
        state.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print("e2e-seed: cannot create %s (%s)" % (state, type(exc).__name__),
              file=sys.stderr)
        return 1
    path = state / "pending.json"
    path.write_text(json.dumps(build_record(args.status, home), indent=2),
                    encoding="utf-8")
    print("E2E-SEED %s %s" % (args.status, path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
