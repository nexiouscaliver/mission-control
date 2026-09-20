"""Shared real-input config + path guards for the mcwall-e2e harness.

Real product inputs (READ-ONLY, never written): the live zcode session db and
the memory-vault program notes. Everything the harness WRITES lives under the
temp MC_WALL_HOME chosen by serve.py.
"""

from __future__ import annotations

import pathlib
import re
import sys

WORKTREE_ROOT = pathlib.Path(__file__).resolve().parents[2]

E2E_PORT = 8799
WALL_JSON_FILENAME = "wall.json"
ENV_FILENAME = "e2e-env.json"
URL_BASE = "http://127.0.0.1:%d" % E2E_PORT

REAL_DB_PATH = pathlib.Path.home() / ".zcode" / "cli" / "db" / "db.sqlite"

# Insertion order == programs order in the tower doc.
VAULT_PROGRAM_NOTES = {
    "mc-wall": pathlib.Path.home() / "work" / "memory-vault" / "shared"
    / "programs" / "mission-control-mc-wall-program.md",
    "secfix": pathlib.Path.home() / "work" / "memory-vault" / "cleo"
    / "mission-control" / "mission-control-secfix-orbit-socials-program.md",
    "det-filter": pathlib.Path.home() / "work" / "memory-vault" / "shared"
    / "programs" / "mission-control-det-filter-program.md",
}

REPO_SPECS = (
    ("cleo", str(pathlib.Path.home() / "work" / "regenai-repo" / "cleo"), "gitlab"),
    ("mc-wall", str(pathlib.Path.home() / ".zcode" / "mc-wall"), "gitlab"),
)

SEED_STATUSES = ("prompt-armed", "goal-armed", "flagged")

# web/index.html embeds each QA mock doc as <script type="application/json"
# id="mock-<case>">…</script> — the same fixtures the page itself reads
# (app.js mock-case lookup). Attribute order is tolerated either way.
_MOCK_SCRIPT_RE = re.compile(
    r'<script\b[^>]*\bid="mock-([a-z0-9-]+)"[^>]*>(.*?)</script>', re.DOTALL
)


def extract_mock_case(index_html_text: str, case: str) -> dict:
    """Parse one embedded QA mock doc out of web/index.html's TEXT (pure).

    Single source of truth: the page's own fixtures, extracted at runtime —
    no duplication in the harness. Returns the parsed JSON object for `case`.
    Raises ValueError (listing the cases actually present) for an unknown
    case, and ValueError for a script body that is not valid JSON.
    """
    import json

    docs = {m.group(1): m.group(2) for m in _MOCK_SCRIPT_RE.finditer(index_html_text)}
    if case not in docs:
        raise ValueError(
            "unknown mock case %r — valid: %s"
            % (case, ", ".join(sorted(docs)) or "(none found)")
        )
    return json.loads(docs[case])


def build_tower_config():
    """REAL TowerConfig over the read-only live inputs (defaults elsewhere)."""
    from mc_wall.tower.config import ProgramConfig, RepoConfig, TowerConfig

    programs = tuple(
        ProgramConfig(program=name, tag=name, note_glob=str(path))
        for name, path in VAULT_PROGRAM_NOTES.items()
    )
    repos = tuple(RepoConfig(name=n, path=p, host=h) for n, p, h in REPO_SPECS)
    return TowerConfig(
        db_path=str(REAL_DB_PATH),
        programs=programs,
        repos=repos,
        pending_launch_path=None,  # prod default; any "launch state degraded" line is recorded truth
    )


def assert_worktree_imports() -> None:
    """The harness must exercise THIS worktree's mc_wall and web/, never the
    main checkout's. An import from elsewhere is a harness bug, exit 5."""
    import mc_wall
    from mc_wall.server import __main__ as entry

    problems = []
    # mc_wall is a PACKAGE: __file__ is <root>/mc_wall/__init__.py, so the
    # worktree root is parents[1] (app.py's _repo_root uses parents[2] only
    # because it sits one level deeper, at mc_wall/server/app.py).
    mc_root = pathlib.Path(mc_wall.__file__).resolve().parents[1]
    if mc_root != WORKTREE_ROOT:
        problems.append(
            "mc_wall imported from %s, not the run worktree %s "
            "(set PYTHONPATH=%s)" % (mc_root, WORKTREE_ROOT, WORKTREE_ROOT)
        )
    entry_root = pathlib.Path(entry.REPO_ROOT).resolve()
    if entry_root != WORKTREE_ROOT:
        problems.append(
            "entry REPO_ROOT is %s, not %s" % (entry_root, WORKTREE_ROOT)
        )
    if not (entry_root / "web" / "index.html").is_file():
        problems.append("served web_dir has no index.html under %s" % entry_root)
    if problems:
        for line in problems:
            print("e2e-common: WORKTREE ASSERT FAILED: %s" % line, file=sys.stderr)
        raise SystemExit(5)


def load_env(home: pathlib.Path) -> dict:
    """Read+validate <home>/e2e-env.json written by serve.py."""
    import json

    path = pathlib.Path(home) / ENV_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SystemExit("e2e-common: cannot read %s — did serve.py boot?" % path)
    if not isinstance(data, dict):
        raise SystemExit("e2e-common: %s is not a JSON object" % path)
    for key in ("home", "port", "token_file", "url_base", "page_url", "pid"):
        if key not in data:
            raise SystemExit("e2e-common: %s missing key %r" % (path, key))
    return data
