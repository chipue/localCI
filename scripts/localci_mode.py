#!/usr/bin/env python3
"""Select a lightweight or integration operation mode without mutating Git."""
from __future__ import annotations

import argparse
import fnmatch
import json
import pathlib
import subprocess
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]


def changed_files(root: pathlib.Path, base: str | None, override: list[str]) -> list[str]:
    if override:
        return sorted(set(override))
    command = ["git", "-C", str(root), "diff", "--name-only"]
    if base:
        command = ["git", "-C", str(root), "diff", "--name-only", base, "HEAD"]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        return []
    return sorted({line.strip() for line in result.stdout.splitlines() if line.strip()})


def load_manifest(path: pathlib.Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("commands", []) if isinstance(data, dict) else []


def choose_mode(files: list[str], commands: list[dict[str, Any]], branch: str | None = None) -> dict[str, Any]:
    reasons: list[str] = []
    if branch and branch.startswith(("integration/", "release/")):
        reasons.append(f"branch={branch} requires integration validation")
    if len(files) > 20:
        reasons.append(f"changed_files={len(files)} exceeds lightweight threshold=20")
    integration_patterns = (".github/", ".localci/", "WORKFLOW.md", "AGENTS.md",
                            "Dockerfile", "docker-compose", "package-lock.json",
                            "poetry.lock", "requirements.txt")
    matched = [path for path in files if any(
        path == pattern or path.startswith(pattern) or fnmatch.fnmatch(path, pattern + "*")
        for pattern in integration_patterns
    )]
    if matched:
        reasons.append("shared CI/runtime configuration changed: " + ", ".join(matched[:5]))
    required_services: set[str] = set()
    requires_docker = False
    product_files = [path for path in files if not (
        path.startswith("docs/") or path.lower().endswith(".md") or path.lower().endswith(".txt")
    )]
    heavy_stages = {"integration_test", "full_ci", "package", "coverage"}
    heavy_commands: list[str] = []
    for item in commands:
        requirements = item.get("requirements", {})
        required_services.update(str(value) for value in requirements.get("services", []))
        requires_docker = requires_docker or bool(requirements.get("docker"))
        if str(item.get("stage")) in heavy_stages:
            heavy_commands.append(str(item.get("name", "unnamed")))
    if required_services and product_files:
        reasons.append("manifest declares services: " + ", ".join(sorted(required_services)))
    if requires_docker and product_files:
        reasons.append("manifest requires Docker")
    if heavy_commands and any(path.startswith(("src/", "server/", "app/", "lib/")) for path in files):
        reasons.append("product code change selects integration-capable commands: " +
                       ", ".join(heavy_commands[:5]))
    integration = bool(reasons)
    return {
        "mode": "integration" if integration else "lightweight",
        "changed_files": files,
        "reasons": reasons or ["small change with no shared-runtime or service requirement"],
        "operations": {
            "worktree": "dedicated/formal worktree recommended" if integration else "current checkout",
            "branch": "register parent and run merge-check" if integration else "current feature branch",
            "profile": "full" if integration else "quick",
            "ci": "plan → execute → merge-check" if integration else "targeted localci run",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="localci mode")
    parser.add_argument("--root", type=pathlib.Path, default=ROOT)
    parser.add_argument("--base")
    parser.add_argument("--inventory", type=pathlib.Path, default=pathlib.Path(".localci/product-commands.json"))
    parser.add_argument("--branch")
    parser.add_argument("--changed-file", action="append", default=[])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    inventory = args.inventory if args.inventory.is_absolute() else root / args.inventory
    try:
        payload = choose_mode(changed_files(root, args.base, args.changed_file),
                              load_manifest(inventory), args.branch)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"mode error: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"localCI mode: {payload['mode']}")
        for reason in payload["reasons"]:
            print(f"  - reason: {reason}")
        for key, value in payload["operations"].items():
            print(f"  - {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
