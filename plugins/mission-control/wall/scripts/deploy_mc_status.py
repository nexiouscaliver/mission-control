"""File-copy deploy helper for the mc-status skill (stdlib only).

``deploy(repo_root, target_dir)`` recursively copies every regular file under
``<repo_root>/skills/mc-status/`` into ``target_dir`` preserving relative
paths (idempotent overwrite; ``os.makedirs(exist_ok=True)`` per directory).
Raises FileNotFoundError when the source dir is missing, returns target_dir.

Consumed two ways: in-process by ``bin/mc-wall``'s ``Cli._deploy_skill`` (the
install hook) and standalone via ``python -m scripts.deploy_mc_status``.
Syntax floor: Python 3.9 (AC-58) — no f-strings, no annotations.
"""

import argparse
import os
import shutil


def deploy(repo_root, target_dir):
    src = os.path.join(repo_root, "skills", "mc-status")
    if not os.path.isdir(src):
        raise FileNotFoundError(src)
    for dirpath, _dirnames, filenames in os.walk(src):
        rel = os.path.relpath(dirpath, src)
        dst_dir = target_dir if rel == "." else os.path.join(target_dir, rel)
        os.makedirs(dst_dir, exist_ok=True)
        for name in filenames:
            src_file = os.path.join(dirpath, name)
            if os.path.isfile(src_file):
                shutil.copyfile(src_file, os.path.join(dst_dir, name))
    return target_dir


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(
        prog="deploy-mc-status",
        description="Copy skills/mc-status into a zcode skills directory.")
    parser.add_argument(
        "--repo-root",
        default=os.path.dirname(here),
        help="checkout root containing skills/mc-status (default: this script's repo)")
    parser.add_argument(
        "--target",
        default=os.path.join(os.path.expanduser("~"), ".zcode", "skills", "mc-status"),
        help="destination directory (default: ~/.zcode/skills/mc-status)")
    args = parser.parse_args(argv)
    print(deploy(args.repo_root, args.target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
