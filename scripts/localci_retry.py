"""Retry only commands marked as rerunnable in a localCI run result."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

from local_executor import execute_plan
from ci_history import event, execution_id
from ci_history_store import append_record, default_history_file
from execution_report import attach_report, write_html, write_json

ROOT = pathlib.Path(__file__).resolve().parents[1]


def order_by_dependencies(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable topological sort: dependencies first, original order as tie-breaker."""
    by_name = {str(item.get("name")): item for item in selected}
    original_index = {name: index for index, name in enumerate(by_name)}
    indegree = {name: 0 for name in by_name}
    dependents = {name: [] for name in by_name}
    for name, item in by_name.items():
        for dependency in item.get("depends_on", []):
            dependency = str(dependency)
            if dependency in by_name:
                indegree[name] += 1
                dependents[dependency].append(name)
    ready = sorted((name for name, count in indegree.items() if count == 0),
                   key=original_index.get)
    ordered_names: list[str] = []
    while ready:
        name = ready.pop(0)
        ordered_names.append(name)
        for dependent in dependents[name]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
                ready.sort(key=original_index.get)
    if len(ordered_names) != len(by_name):
        cycle = sorted(name for name, count in indegree.items() if count > 0)
        raise ValueError("retry candidate dependency cycle: " + ", ".join(cycle))
    return [by_name[name] for name in ordered_names]


def make_retry_plan(
    payload: dict[str, Any], failed_only: bool = False, stage: str | None = None
) -> dict[str, Any]:
    plan = payload.get("plan")
    if not isinstance(plan, dict):
        raise ValueError("input must be a localci run JSON result containing plan")
    result_payload = payload.get("result") or {}
    summary = result_payload.get("execution_summary", {})
    candidates = summary.get("rerun_candidates", payload.get("rerun_candidates", []))
    if not isinstance(candidates, list) or not all(isinstance(name, str) for name in candidates):
        raise ValueError("result.execution_summary.rerun_candidates must be a list of names")
    candidate_names = set(candidates)
    previous_results = result_payload.get("results", [])
    result_by_name = {
        item.get("name"): item for item in previous_results if isinstance(item, dict)
    }
    if failed_only:
        candidate_names &= {
            name for name, item in result_by_name.items()
            if item.get("status") in {"failed", "timeout"}
        }
    if stage:
        candidate_names = {
            name for name in candidate_names
            if next((item.get("stage") for item in plan.get("selected", [])
                     if item.get("name") == name), None) == stage
        }
    selected = [item for item in plan.get("selected", []) if item.get("name") in candidate_names]
    if not selected and candidate_names:
        selected = [
            blocked.get("item") for blocked in plan.get("blocked", [])
            if blocked.get("name") in candidate_names and isinstance(blocked.get("item"), dict)
        ]
    selected = order_by_dependencies(selected)
    blocked_retry = bool(plan.get("blocked")) and not result_payload
    return {
        **plan,
        "selected": selected,
        "blocked": plan.get("blocked", []) if blocked_retry else [],
        "execution_allowed": False if blocked_retry else True,
        "retry_of": result_payload.get("log_dir"),
        "blocked_retry": blocked_retry,
        "retry_filter": {"failed_only": failed_only, "stage": stage},
        "retry_order": [item.get("name") for item in selected],
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="localci retry")
    parser.add_argument("result", type=pathlib.Path, help="JSON result produced by localci run --json")
    parser.add_argument("--root", type=pathlib.Path, default=ROOT)
    parser.add_argument("--log-dir", type=pathlib.Path, help="Base directory for retry logs")
    parser.add_argument("--failed-only", action="store_true", help="Retry only failed or timed-out commands")
    parser.add_argument("--stage", help="Retry only commands in this stage")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--report-json", type=pathlib.Path, help="Write the complete shareable retry report JSON to this path")
    parser.add_argument("--report-html", type=pathlib.Path, help="Write a shareable HTML retry report to this path")
    parser.add_argument("--history-file", type=pathlib.Path, default=default_history_file(), help="Append compact execution history here")
    args = parser.parse_args()
    try:
        payload = json.loads(args.result.read_text(encoding="utf-8"))
        plan = make_retry_plan(payload, args.failed_only, args.stage)
        if plan.get("blocked_retry"):
            blocked_items = [item for item in plan["blocked"] if item.get("name") in plan["retry_order"]]
            result = {
                "backend": plan.get("backend"),
                "backend_execution": {"backend": plan.get("backend"), "modes": {},
                                       "native_commands": 0, "fallback_commands": 0},
                "log_dir": None,
                "failure_summary": None,
                "execution_summary": {
                    "total": len(blocked_items), "completed": 0, "success": 0,
                    "failed": 0, "timeout": 0, "not_run": len(blocked_items),
                    "blocked": len(blocked_items),
                    "commands": [{"name": item["name"],
                                  "stage": item.get("item", {}).get("stage", "unknown"),
                                  "status": "blocked", "reason": "backend_requirements_unavailable"}
                                 for item in blocked_items],
                    "rerun_candidates": [item["name"] for item in blocked_items],
                },
                "status": "blocked",
                "results": [{"name": item["name"],
                             "stage": item.get("item", {}).get("stage", "unknown"),
                             "status": "blocked", "returncode": None,
                             "reason": "backend_requirements_unavailable", "rerunnable": True,
                             "missing": item.get("missing", [])}
                            for item in blocked_items],
            }
        elif plan["selected"]:
            result = execute_plan(plan, args.root.resolve(), args.log_dir.resolve() if args.log_dir else None)
        else:
            result = {
                "backend": plan.get("backend"),
                "log_dir": None,
                "failure_summary": None,
                "execution_summary": {
                    "total": 0, "completed": 0, "success": 0, "failed": 0,
                    "timeout": 0, "not_run": 0, "commands": [], "rerun_candidates": [],
                },
                "status": "no_candidates",
                "results": [],
            }
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        print(f"retry error: {error}", file=sys.stderr)
        return 2

    output = {"retry_plan": plan, "result": result}
    output["execution_id"] = execution_id("retry")
    output["history"] = list(payload.get("history", []))
    output["history"].append(event(
        "retry_started", source_execution_id=payload.get("execution_id"),
        backend=plan.get("backend"), candidates=plan.get("retry_order", []),
    ))
    output["history"].append(event("retry_completed", status=result["status"], backend=result.get("backend")))
    route_status = (
        "blocked" if result["status"] == "blocked"
        else "selected" if result["status"] != "no_candidates"
        else "no_candidates"
    )
    output["route"] = {
        "requested": "retry",
        "selected": plan.get("backend"),
        "status": route_status,
        "source": payload.get("route"),
    }
    attach_report(output)
    if args.report_json:
        output["report_json"] = str(args.report_json.resolve())
    if args.report_html:
        output["report_html"] = str(args.report_html.resolve())
    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print("localCI retry")
        print(f"selected: {len(plan['selected'])}")
        print(f"execution: {result['status']}")
        if result["log_dir"]:
            print(f"logs: {result['log_dir']}")
        for item in result["results"]:
            print(f"  - {item['name']}: {item['status']}")
    try:
        append_record(output, args.history_file, "retry")
    except OSError as error:
        print(f"history error: {error}", file=sys.stderr)
        return 2
    if args.report_json:
        write_json(output, args.report_json)
    if args.report_html:
        write_html(output, args.report_html)
    return 0 if result["status"] in {"success", "no_candidates", "blocked"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
