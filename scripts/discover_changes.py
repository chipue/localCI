#!/usr/bin/env python3
"""Discover tracked and untracked Git changes for the CI Router."""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]


def git(root: pathlib.Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    return result.stdout


def parse_status(data: bytes) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for raw in data.split(b"\0"):
        if not raw:
            continue
        text = raw.decode("utf-8", errors="surrogateescape")
        status = text[:2]
        path = text[3:] if len(text) > 3 else ""
        records.append({"path": path, "status": status, "source": "working-tree"})
    return records


def parse_diff(data: bytes, base: str) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    fields = data.split(b"\0")
    index = 0
    while index < len(fields):
        raw = fields[index]
        index += 1
        if not raw:
            continue
        parts = raw.decode("utf-8", errors="surrogateescape").split("\t", 1)
        status = parts[0]
        path = parts[1] if len(parts) == 2 else ""
        if status.startswith("R") or status.startswith("C"):
            if index < len(fields) and fields[index]:
                old_path = path
                path = fields[index].decode("utf-8", errors="surrogateescape")
                index += 1
                records.append({"path": path, "status": status, "source": f"base:{base}", "previous_path": old_path})
                continue
        records.append({"path": path, "status": status, "source": f"base:{base}"})
    return records


def discover(root: pathlib.Path, base: str | None) -> dict[str, Any]:
    try:
        status_data = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    except (OSError, subprocess.CalledProcessError):
        # A cloud-backed or temporarily unavailable Git metadata directory must
        # not make the Router silently select a narrow test subset. Returning
        # an unknown source lets callers fall back to the full eligible plan.
        return {
            "root": str(root), "base": base, "count": 0, "changed_files": [],
            "source": "git-unavailable",
        }
    records = parse_status(status_data)
    if base:
        records.extend(parse_diff(git(root, "diff", "--name-status", "-z", f"{base}...HEAD"), base))
    unique: dict[str, dict[str, str]] = {}
    for record in records:
        unique[record["path"]] = record
    files = [unique[path] for path in sorted(unique)]
    return {
        "root": str(root),
        "base": base,
        "count": len(files),
        "changed_files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, default=ROOT)
    parser.add_argument("--base", help="include committed changes from BASE...HEAD")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()
    try:
        result = discover(args.root.resolve(), args.base)
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"change discovery failed: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"changed files: {result['count']}")
        for item in result["changed_files"]:
            previous = f" <- {item['previous_path']}" if "previous_path" in item else ""
            print(f"{item['status']} {item['path']}{previous} ({item['source']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
