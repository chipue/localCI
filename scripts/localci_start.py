#!/usr/bin/env python3
"""Start the smallest appropriate localCI run without Git/worktree mutation."""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from typing import Any

from localci_mode import changed_files, choose_mode, load_manifest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def current_branch(root: pathlib.Path) -> str | None:
    result = subprocess.run(["git", "-C", str(root), "branch", "--show-current"],
                            text=True, capture_output=True, check=False)
    branch = result.stdout.strip()
    return branch or None


def main() -> int:
    parser = argparse.ArgumentParser(prog="localci start")
    parser.add_argument("--root", type=pathlib.Path, default=ROOT)
    parser.add_argument("--base")
    parser.add_argument("--branch")
    parser.add_argument("--mode", choices=("auto", "lightweight", "integration"), default="auto")
    parser.add_argument("--backend", choices=("auto", "host", "docker", "act", "wsl", "windows"), default="auto")
    parser.add_argument("--inventory", type=pathlib.Path, default=pathlib.Path(".localci/product-commands.json"))
    parser.add_argument("--history-file", type=pathlib.Path)
    parser.add_argument("--log-dir", type=pathlib.Path)
    parser.add_argument("--result-file", type=pathlib.Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--changed-file", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    inventory = args.inventory if args.inventory.is_absolute() else root / args.inventory
    branch = args.branch or current_branch(root)
    policy = choose_mode(changed_files(root, args.base, args.changed_file),
                         load_manifest(inventory), branch)
    selected_mode = policy["mode"] if args.mode == "auto" else args.mode
    profile = "quick" if selected_mode == "lightweight" else "full"
    selected_backend = "host" if args.dry_run and args.backend == "auto" else args.backend
    command = ["bash", str(ROOT / "localci"), "plan" if args.dry_run else "run",
               "--root", str(root), "--inventory", str(inventory),
               "--profile", profile, "--backend", selected_backend, "--json"]
    if args.base:
        command.extend(["--base", args.base])
    if args.history_file and not args.dry_run:
        command.extend(["--history-file", str(args.history_file.resolve())])
    if args.log_dir and not args.dry_run:
        command.extend(["--log-dir", str(args.log_dir.resolve())])
    if args.result_file and not args.dry_run:
        command.extend(["--result-file", str(args.result_file.resolve())])
    completed = subprocess.run(command, cwd=root, text=True, capture_output=True, check=False)
    try:
        execution = json.loads(completed.stdout) if completed.stdout.strip() else None
    except json.JSONDecodeError:
        execution = None
    payload: dict[str, Any] = {
        "operation_mode": selected_mode,
        "selected_profile": profile,
        "branch": branch,
        "policy": policy,
        "mutations": {"fetch": False, "worktree": False, "branch": False},
        "dry_run": args.dry_run,
        "execution": execution,
        "stdout": None if execution is not None else completed.stdout,
        "stderr": completed.stderr,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"localCI start: {selected_mode} (profile={profile})")
        print("  - fetch: skipped")
        print("  - worktree: current checkout")
        print("  - branch creation: skipped")
        print(f"  - CI: {'plan' if args.dry_run else 'run'}")
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
