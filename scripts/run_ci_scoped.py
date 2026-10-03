#!/usr/bin/env python3
"""Run localCI only for changes relative to the current branch's parent."""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

try:
    from .branch_policy import current_branch, validate
except ImportError:
    from branch_policy import current_branch, validate

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(prog="run_ci_scoped")
    parser.add_argument("--base", help="Override the parent branch")
    parser.add_argument("--profile", default="standard", choices=("quick", "standard", "full", "delivery"))
    parser.add_argument("--backend", default="auto")
    args = parser.parse_args()
    try:
        branch = current_branch()
        parent = args.base or validate(branch)
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        print(f"scoped CI blocked: {error}", file=sys.stderr)
        return 2
    if not parent:
        print("scoped CI blocked: a parent branch is required", file=sys.stderr)
        return 2
    environment = {
        **os.environ,
        "AGENT_CI_BASE": parent,
        "AGENT_CI_PROFILE": args.profile,
        "AGENT_CI_BACKEND": args.backend,
    }
    return subprocess.run(["bash", str(ROOT / "scripts/run_ci_local_quiet.sh")],
                          cwd=ROOT, env=environment).returncode


if __name__ == "__main__":
    raise SystemExit(main())
