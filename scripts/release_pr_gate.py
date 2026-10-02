#!/usr/bin/env python3
"""Validate the explicit, release-PR-only gate inputs."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
from typing import Any


def check(*, local_ci_passed: str, merge_authorized: str, source_branch: str, target_branch: str) -> dict[str, Any]:
    checks = {
        "local_ci_passed": local_ci_passed.lower() == "true",
        "merge_authorized": merge_authorized.lower() == "true",
        "source_is_child": bool(source_branch) and source_branch != "main",
        "target_is_main": target_branch == "main",
    }
    return {"status": "passed" if all(checks.values()) else "blocked", "checks": checks,
            "source_branch": source_branch, "target_branch": target_branch}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-ci-passed", default=os.environ.get("LOCAL_CI_PASSED", "false"))
    parser.add_argument("--merge-authorized", default=os.environ.get("MERGE_AUTHORIZED", "false"))
    parser.add_argument("--source-branch", default=os.environ.get("SOURCE_BRANCH", ""))
    parser.add_argument("--target-branch", default=os.environ.get("TARGET_BRANCH", ""))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args()
    result = check(local_ci_passed=args.local_ci_passed, merge_authorized=args.merge_authorized,
                   source_branch=args.source_branch, target_branch=args.target_branch)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text if args.json else f"release PR gate: {result['status']}")
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
