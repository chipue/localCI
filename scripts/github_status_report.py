#!/usr/bin/env python3
"""Emit a compact GitHub-compatible status report without requiring the gh CLI."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
from typing import Any


def build_report(result: dict[str, Any], *, context: str) -> dict[str, Any]:
    execution = result.get("result") or result
    status = str(execution.get("status", "unknown"))
    conclusion = "success" if status in {"success", "passed", "cached"} else "failure"
    return {
        "schema_version": 1,
        "context": context,
        "state": "success" if conclusion == "success" else "failure",
        "description": f"localCI {status}",
        "target_url": os.environ.get("GITHUB_SERVER_URL", "")
        + (f"/{os.environ.get('GITHUB_REPOSITORY', '')}/actions" if os.environ.get("GITHUB_REPOSITORY") else ""),
        "execution_id": result.get("execution_id") or execution.get("run_id"),
        "profile": (result.get("plan") or {}).get("profile"),
        "duration_seconds": (result.get("report") or {}).get("duration_seconds")
        or execution.get("duration_seconds"),
        "summary": (result.get("report") or {}).get("summary")
        or execution.get("execution_summary", {}),
    }


def write_summary(report: dict[str, Any]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    path = pathlib.Path(summary_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "## localCI\n\n"
        f"- context: `{report['context']}`\n"
        f"- state: `{report['state']}`\n"
        f"- description: {report['description']}\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path)
    parser.add_argument("--context", default="localci/delivery")
    args = parser.parse_args()
    result = json.loads(args.input.read_text(encoding="utf-8"))
    report = build_report(result, context=args.context)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_summary(report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["state"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
