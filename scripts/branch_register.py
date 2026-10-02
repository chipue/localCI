#!/usr/bin/env python3
"""Register the immediate parent for an arbitrarily deep branch."""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

try:
    from .branch_policy import PARENTS_FILE, current_branch, is_ancestor
except ImportError:
    from branch_policy import PARENTS_FILE, current_branch, is_ancestor


def find_cycle(mapping: dict[str, str]) -> list[str] | None:
    """Return one cycle in child -> parent mappings, if present."""
    for start in mapping:
        path: list[str] = []
        positions: dict[str, int] = {}
        current = start
        while current in mapping:
            if current in positions:
                return path[positions[current]:] + [current]
            positions[current] = len(path)
            path.append(current)
            current = mapping[current]
    return None


def validate_registration(mapping: dict[str, str], child: str, parent: str) -> None:
    if not child or not parent:
        raise ValueError("child and parent branch names are required")
    if child == parent:
        raise ValueError("a branch cannot be its own parent")
    candidate = {str(key): str(value) for key, value in mapping.items()}
    candidate[child] = parent
    cycle = find_cycle(candidate)
    if cycle:
        raise ValueError("branch parent cycle: " + " -> ".join(cycle))


def main() -> int:
    parser = argparse.ArgumentParser(prog="localci branch register")
    parser.add_argument("--parent", required=True)
    parser.add_argument("--branch", help="Child branch; defaults to the current branch")
    args = parser.parse_args()
    branch = args.branch or current_branch()
    if branch == "main":
        print("branch register failed: main cannot have a child mapping", file=sys.stderr)
        return 2
    try:
        subprocess.run(["git", "rev-parse", "--verify", args.parent], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        subprocess.run(["git", "rev-parse", "--verify", branch], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if not is_ancestor(args.parent, branch):
            raise ValueError(f"parent branch {args.parent} is not an ancestor of {branch}")
        data = {}
        if PARENTS_FILE.exists():
            loaded = json.loads(PARENTS_FILE.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError(f"{PARENTS_FILE} must contain an object")
            data.update(loaded)
        validate_registration(data, branch, args.parent)
        data[branch] = args.parent
        PARENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        PARENTS_FILE.write_text(json.dumps(dict(sorted(data.items())), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, json.JSONDecodeError, subprocess.CalledProcessError, ValueError) as error:
        print(f"branch register failed: {error}", file=sys.stderr)
        return 2
    print(f"registered parent: {branch} <- {args.parent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
