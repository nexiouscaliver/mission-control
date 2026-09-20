"""T15 — structural constraints over prod sources and test hygiene.

AC-57: bin/mc-wall + every .py under mc_wall/server imports stdlib or
mc_wall only (relative imports allowed). AC-58: the same file set parses
with ast feature_version=(3, 9). AC-56 (orchestrator-reconciled
predicates): every tests/server source file is test_mcwalls_*.py or the
mcwalls_harness.py helper (plus the empty __init__.py package marker that
makes `tests.server.*` imports work); the spawn-capable stdlib module (see
SPAWN_MODULE) is imported only by test_mcwalls_entry.py, whose Popen argv
must be [sys.executable, "-m", ...] and never shell=True; and the
home-expansion calls (see EXPANDUSER / PATH_HOME) have ZERO occurrences
anywhere under tests/server, harness included.

Needles are built by concatenation so this file cannot match itself.
"""

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_DIR = REPO_ROOT / "mc_wall" / "server"
TESTS_DIR = REPO_ROOT / "tests" / "server"
ENTRY_TEST = "test_mcwalls_entry.py"

# Concatenated so this sweep file itself stays occurrence-free.
SPAWN_MODULE = "sub" + "process"
EXPANDUSER = "expand" + "user"
PATH_HOME = "Path" + ".home"


def _prod_files():
    files = [REPO_ROOT / "bin" / "mc-wall"]
    files += sorted(p for p in SERVER_DIR.rglob("*.py") if p.is_file())
    return files


def _test_source_files():
    return sorted(
        p
        for p in TESTS_DIR.rglob("*.py")
        if p.is_file() and "__pycache__" not in p.parts
    )


def _import_roots(tree):
    """Yield the root module name of every Import/ImportFrom in tree.

    Relative imports (level > 0) stay inside the package and are skipped.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                continue
            yield (node.module or "").split(".")[0]


def _is_sys_executable(node):
    # The expression `sys.executable`.
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "executable"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    )


def _is_dash_m(node):
    return isinstance(node, ast.Constant) and node.value == "-m"


def _argv_starts_with_interpreter(elts):
    # [sys.executable, "-m", ...]
    return bool(elts) and _is_sys_executable(elts[0]) and len(elts) > 1 and _is_dash_m(elts[1])


def test_prod_imports_stdlib_only():
    allowed_roots = set(sys.stdlib_module_names) | {"mc_wall"}
    offenders = []
    for path in _prod_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for root in _import_roots(tree):
            if root not in allowed_roots:
                offenders.append((str(path.relative_to(REPO_ROOT)), root))
    assert offenders == [], f"non-stdlib/non-mc_wall imports: {offenders}"


def test_prod_parses_at_py39():
    for path in _prod_files():
        source = path.read_text(encoding="utf-8")
        try:
            ast.parse(source, filename=str(path), feature_version=(3, 9))
        except SyntaxError as exc:
            raise AssertionError(f"{path} is not 3.9-parseable: {exc}") from None


def test_test_hygiene_structure():
    # (1) Naming: everything directly under tests/server is a test_mcwalls_*
    # module, the mcwalls_harness.py helper, or the empty __init__.py package
    # marker (T1) that `from tests.server.mcwalls_harness import ...` needs.
    top = sorted(p.name for p in TESTS_DIR.iterdir() if p.is_file())
    violations = [
        n
        for n in top
        if not (
            (n.startswith("test_mcwalls_") and n.endswith(".py"))
            or n == "mcwalls_harness.py"
            or n == "__init__.py"
        )
    ]
    assert violations == [], f"non-conforming files under tests/server: {violations}"
    assert "mcwalls_harness.py" in top
    assert any(n.startswith("test_mcwalls_") for n in top)

    # (2) Spawn ban (scoped): no tests/server source imports the spawn-capable
    # stdlib module except the entry e2e, which must spawn ONLY the current
    # interpreter: Popen argv[0] == sys.executable with "-m" next, never a
    # shell. Names bound to such a list (argv = [sys.executable, "-m", ...])
    # are accepted — checked by source read, not just by trusting the runtime.
    sources = {p.name: p for p in _test_source_files()}
    for name, path in sources.items():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if name != ENTRY_TEST:
            roots = list(_import_roots(tree))
            assert SPAWN_MODULE not in roots, f"{name} imports the banned spawn module"
            continue

        # ENTRY_TEST: the spawn module must be imported AND used via Popen.
        roots = list(_import_roots(tree))
        assert SPAWN_MODULE in roots, "entry e2e must import the spawn module"
        interpreter_argv_names = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.List)
                and _argv_starts_with_interpreter(node.value.elts)
            ):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        interpreter_argv_names.add(target.id)
        popens = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and (
                (isinstance(n.func, ast.Attribute) and n.func.attr == "Popen")
                or (isinstance(n.func, ast.Name) and n.func.id == "Popen")
            )
        ]
        assert popens, "entry e2e must spawn via Popen([sys.executable, '-m', ...])"
        for call in popens:
            assert call.args, "Popen must receive an explicit argv"
            first = call.args[0]
            if isinstance(first, ast.List):
                assert _argv_starts_with_interpreter(first.elts), (
                    "Popen argv must be [sys.executable, '-m', ...]"
                )
            else:
                assert isinstance(first, ast.Name) and first.id in interpreter_argv_names, (
                    "Popen argv must be [sys.executable, '-m', ...] (directly or "
                    "via a name bound to such a list)"
                )
            for kw in call.keywords:
                assert not (
                    kw.arg == "shell"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value
                ), "Popen must never pass shell=True"
        entry_src = path.read_text(encoding="utf-8")
        assert "shell=True" not in entry_src

    # (3) Home-dir resolution ban: ZERO occurrences anywhere under
    # tests/server — harness included, no exceptions.
    for name, path in sources.items():
        src = path.read_text(encoding="utf-8", errors="replace")
        assert EXPANDUSER not in src, f"{name} resolves against the user home"
        assert PATH_HOME not in src, f"{name} resolves against the user home"
