#!/usr/bin/env python3
"""Run the delivery profile's trigger-specific checks and publish one report."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile
from typing import Any

from github_status_report import build_report
from release_pr_gate import check as check_release_gate


ROOT = pathlib.Path(__file__).resolve().parents[1]


def run_localci(profile: str, *, diff: bool) -> tuple[int, dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="localci-delivery-") as directory:
        result_file = pathlib.Path(directory) / "result.json"
        command = [str(ROOT / "localci"), "run", "--profile", profile, "--backend", "host",
                   "--result-file", str(result_file), "--json"]
        if diff:
            command.append("--diff")
        base = os.environ.get("AGENT_CI_BASE")
        if base:
            command.extend(["--base", base])
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
        if result_file.exists():
            result = json.loads(result_file.read_text(encoding="utf-8"))
        else:
            result = {"error": completed.stderr[-2000:], "result": {"status": "failed"}}
        return completed.returncode, result


def run(event: str) -> dict[str, Any]:
    if event == "release-pr":
        gate = check_release_gate(
            local_ci_passed=os.environ.get("LOCAL_CI_PASSED", "false"),
            merge_authorized=os.environ.get("MERGE_AUTHORIZED", "false"),
            source_branch=os.environ.get("SOURCE_BRANCH", ""),
            target_branch=os.environ.get("TARGET_BRANCH", ""),
        )
        if gate["status"] != "passed":
            return {"event": event, "status": "blocked", "release_gate": gate}
        exit_code, result = run_localci("full", diff=False)
    elif event == "nightly":
        gate = None
        exit_code, result = run_localci("full", diff=False)
    else:
        gate = None
        exit_code, result = run_localci("standard", diff=True)
    compact = result.get("result") or result
    status = str(compact.get("status", "failed"))
    report = build_report(result, context=f"localci/delivery/{event}")
    return {"event": event, "status": status if exit_code == 0 else "failed",
            "release_gate": gate, "execution_id": result.get("execution_id"),
            "report": report}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", choices=("push", "nightly", "release-pr"), default="push")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    payload = run(args.event)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
