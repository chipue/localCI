#!/usr/bin/env python3
"""Validate and resolve the main -> integration -> feature branch hierarchy."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PARENTS_FILE = pathlib.Path(
    os.environ.get("LOCALCI_BRANCH_PARENTS_FILE", str(ROOT / ".localci/branch-parents.json"))
)


def git(*arguments: str) -> str:
    return subprocess.run(["git", *arguments], cwd=ROOT, check=True,
                          text=True, capture_output=True).stdout.strip()


def current_branch() -> str:
    return git("branch", "--show-current")


def is_ancestor(ancestor: str, descendant: str = "HEAD") -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, descendant],
                          cwd=ROOT).returncode == 0


def local_branches(prefix: str) -> list[str]:
    output = git("for-each-ref", "--format=%(refname:short)", f"refs/heads/{prefix}")
    return [line for line in output.splitlines() if line]


def registered_parents() -> dict[str, str]:
    if not PARENTS_FILE.exists():
        return {}
    data = json.loads(PARENTS_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{PARENTS_FILE} must contain an object")
    return {str(child): str(parent) for child, parent in data.items()}


def resolve_parent(branch: str) -> str | None:
    configured = os.environ.get("LOCALCI_PARENT_BRANCH")
    if configured:
        return configured
    if branch == "main":
        return None
    registered = registered_parents().get(branch)
    if registered:
        return registered
    # A nested branch such as feature/router/graph prefers the longest
    # existing branch prefix, so arbitrary-depth work remains deterministic.
    prefix_candidates = [candidate for candidate in git("for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines()
                         if candidate and candidate != branch and branch.startswith(candidate + "/")
                         and is_ancestor(candidate)]
    if prefix_candidates:
        return max(prefix_candidates, key=len)
    if branch.startswith("integration/") and is_ancestor("main"):
        return "main"
    candidate_branches = git("for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines()
    if branch.startswith("feature/"):
        candidate_branches = [candidate for candidate in candidate_branches
                              if candidate.startswith("integration/")]
    candidates = [candidate for candidate in candidate_branches
                  if candidate and candidate != branch and candidate != "main" and is_ancestor(candidate)]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates and is_ancestor("main"):
        return "main"
    raise ValueError("branch parent is ambiguous; register it or set LOCALCI_PARENT_BRANCH")


def validate(branch: str | None = None) -> str | None:
    branch = branch or current_branch()
    if branch == "main":
        raise ValueError("direct work or push on main is prohibited")
    parent = resolve_parent(branch)
    if parent and not is_ancestor(parent):
        raise ValueError(f"parent branch {parent} is not an ancestor of {branch}")
    return parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--branch")
    parser.add_argument("--parent-only", action="store_true")
    args = parser.parse_args()
    try:
        branch = args.branch or current_branch()
        parent = validate(branch)
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        print(f"branch policy failed: {error}", file=sys.stderr)
        return 2
    print(parent or "") if args.parent_only else print(f"branch policy passed: {branch} (parent: {parent})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
