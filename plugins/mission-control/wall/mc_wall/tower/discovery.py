"""Boot-time program-note discovery (mcwall-tower-discovery; decisions 1-5, 9)."""

from __future__ import annotations
import os, re, subprocess
from dataclasses import dataclass
from typing import Callable, Sequence

from . import notes
from .config import ProgramConfig, RepoConfig

DEFAULT_SEARCH_DIR = "~/work/memory-vault/shared/programs"
FILENAME_RE = re.compile(r"^mission-control-(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)-program\.md$")
_OFF = frozenset({"0", "off", "no", "false"})


@dataclass(frozen=True)
class DiscoveryResult:
    programs: tuple[ProgramConfig, ...] = ()
    repos: tuple[RepoConfig, ...] = ()
    degraded: tuple[str, ...] = ()


def enabled() -> bool:
    return os.environ.get("MC_WALL_DISCOVERY", "").strip().lower() not in _OFF


def search_dirs(declared: Sequence[ProgramConfig]) -> list[str]:
    dirs = {os.path.expanduser(os.path.dirname(p.note_glob)) for p in declared}
    dirs.discard("")
    return sorted(dirs) or [os.path.expanduser(DEFAULT_SEARCH_DIR)]


def _run_git(path: str) -> tuple[int, str]:
    try:
        r = subprocess.run(["git", "-C", path, "remote", "-v"],
                           capture_output=True, text=True, timeout=5.0)
        return (r.returncode, r.stdout)
    except (OSError, subprocess.TimeoutExpired):
        return (1, "")


def discover_programs(declared, run_git=None, *, declared_repos=()):
    try:
        return _scan(declared, declared_repos, run_git or _run_git)
    except Exception as e:
        return DiscoveryResult((), (), (f"discovery degraded: internal error ({type(e).__name__})",))


def _scan(declared, declared_repos, run_git):
    declared_slugs = {p.program for p in declared}
    found: dict[str, tuple[str, notes.NoteParse]] = {}   # slug -> (abs path, parse)
    degraded: list[str] = []
    for d in search_dirs(declared):
        try:
            with os.scandir(d) as it:              # never glob (decision 3); ctx-mgr closes the iter
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            continue            # heuristic scan: missing/unreadable dir is silent
        for ent in entries:
            m = FILENAME_RE.match(ent.name)
            if m is None or not ent.is_file(follow_symlinks=True):
                continue        # not a candidate: silent
            slug, path = m.group("slug"), os.path.join(d, ent.name)
            if slug in declared_slugs:
                continue        # declared wins: silent
            if slug in found:
                degraded.append(f"discovery degraded: duplicate program {slug} at {path}")
                continue
            try:
                with open(path, "rb") as fh:
                    text = fh.read().decode("utf-8")
            except (OSError, UnicodeDecodeError):
                degraded.append(f"discovery degraded: unreadable note {path}")
                continue
            parsed = notes.parse_note(text)
            if not parsed.header_found:
                degraded.append(f"discovery degraded: no prompt-log table in {path}")
                continue
            if parsed.objective == "":
                degraded.append(f"discovery degraded: no objective in {path}")
                continue
            found[slug] = (path, parsed)
    programs = tuple(ProgramConfig(program=s, tag=s, note_glob=found[s][0], master_tag=None)
                     for s in sorted(found))
    ordered = [(found[s][0], found[s][1], s) for s in sorted(found)]
    repos = _derive_repos(ordered, declared_repos, run_git, degraded)
    return DiscoveryResult(programs, repos, tuple(degraded))


def _derive_repos(ordered, declared_repos, run_git, degraded):
    return ()  # td2 replaces this body (R2+R3)
