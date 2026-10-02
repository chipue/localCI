"""Build a CI plan and execute it through the Local Executor."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

from local_executor import execute_plan
from localci_plan import make_plan
from plan_gate import PlanBlockedError
from ci_history import event, execution_id
from ci_history_store import append_record, default_history_file, read_records
from backend_adapters import BACKENDS
from reproducibility import collect, write_record
from execution_report import attach_report, write_html, write_json

ROOT = pathlib.Path(__file__).resolve().parents[1]
AUTO_BACKENDS = BACKENDS


def route_plan(
    root: pathlib.Path, inventory: pathlib.Path, profile: str, backend: str,
    base: str | None, history_file: pathlib.Path | None = None, diff: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if backend != "auto":
        plan = make_plan(root, inventory, profile, backend, base, diff)
        return plan, {"requested": backend, "selected": backend, "status": "selected", "candidates": []}
    candidates = []
    history = read_records(history_file or default_history_file())
    history_by_backend: dict[str, list[dict[str, Any]]] = {candidate: [] for candidate in AUTO_BACKENDS}
    for record in history:
        if record.get("backend") in history_by_backend:
            history_by_backend[record["backend"]].append(record)
    selected_plan = None
    selected_backend = None
    for candidate in AUTO_BACKENDS:
        candidate_plan = make_plan(root, inventory, profile, candidate, base, diff)
        records = history_by_backend[candidate]
        failed = sum(item.get("status") in {"failed", "blocked"} for item in records)
        durations = [item["duration_seconds"] for item in records
                     if isinstance(item.get("duration_seconds"), (int, float))]
        total_commands = sum(
            (item.get("execution_summary") or {}).get("total", 0)
            for item in records
            if isinstance((item.get("execution_summary") or {}).get("total", 0), (int, float))
        )
        fallback_commands = sum(
            item.get("fallback_commands", 0) for item in records
            if isinstance(item.get("fallback_commands", 0), (int, float))
        )
        failure_rate = failed / len(records) if records else 0.0
        fallback_rate = fallback_commands / total_commands if total_commands else 0.0
        average_duration = sum(durations) / len(durations) if durations else 0.0
        # An unmeasured backend must not outrank the stable default merely
        # because its empty history has a zero score. Once it has a run,
        # measured reliability and duration can improve its route priority.
        score = round(failure_rate * 1000 + fallback_rate * 100 + average_duration, 3) if records else 100.0
        candidates.append({
            "backend": candidate,
            "execution_allowed": candidate_plan["execution_allowed"],
            "runtime_available": candidate_plan["capabilities"].get("runtime_available", True),
            "selected": [item["name"] for item in candidate_plan["selected"]],
            "blocked": candidate_plan["blocked"],
            "history": {"runs": len(records), "failure_rate": round(failure_rate, 3),
                        "average_duration_seconds": round(average_duration, 3),
                        "fallback_rate": round(fallback_rate, 3)},
            "score": score,
        })
        if candidate_plan["execution_allowed"] and candidate_plan["selected"]:
            if selected_plan is None:
                selected_plan = candidate_plan
                selected_backend = candidate
            else:
                current = next(item for item in candidates if item["backend"] == selected_backend)
                if (score, AUTO_BACKENDS.index(candidate)) < (current["score"], AUTO_BACKENDS.index(selected_backend)):
                    selected_plan = candidate_plan
                    selected_backend = candidate
    if selected_plan is None:
        selected_plan = make_plan(root, inventory, profile, "host", base, diff)
        status = "blocked"
    else:
        status = "selected"
    if selected_backend:
        selected_candidate = next(item for item in candidates if item["backend"] == selected_backend)
        selection_reason = (
            f"auto selected {selected_backend}: lowest route score {selected_candidate['score']} "
            f"among executable candidates"
        )
    else:
        selection_reason = "auto routing blocked: no backend can execute the selected commands"
    return selected_plan, {
        "requested": "auto", "selected": selected_backend, "status": status,
        "candidates": candidates, "selection_reason": selection_reason,
    }


def run(
    root: pathlib.Path, inventory: pathlib.Path, profile: str, backend: str,
    base: str | None, log_dir: pathlib.Path | None = None,
    history_file: pathlib.Path | None = None, diff: bool = False,
    use_cache: bool = True, invalidate_cache: bool = False, max_jobs: int | None = None,
    limits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plan, route = route_plan(root, inventory, profile, backend, base, history_file, diff)
    run_id = execution_id("run")
    reproducibility = collect(root, inventory, plan, route)
    if backend != "auto":
        route["selection_reason"] = f"backend explicitly requested: {backend}"
        reproducibility["selection"]["reason"] = route["selection_reason"]
    history = [event("route_selected", route=route)]
    if route["status"] == "blocked":
        history.append(event("execution_blocked", reason="no backend can execute selected product commands"))
        return {
            "execution_id": run_id,
            "reproducibility": reproducibility,
            "history": history,
            "route": route,
            "plan": plan,
            "result": None,
            "rerun_candidates": [item["name"] for item in plan.get("blocked", [])],
            "error": "no backend can execute selected product commands",
        }
    try:
        history.append(event("execution_started", backend=plan.get("backend")))
        result = execute_plan(plan, root, log_dir, use_cache, invalidate_cache,
                              max_jobs=max_jobs, limits=limits, run_id=run_id)
    except PlanBlockedError as error:
        history.append(event("execution_blocked", reason=str(error)))
        return {"execution_id": run_id, "reproducibility": reproducibility, "history": history, "route": route, "plan": plan,
                "result": None,
                "rerun_candidates": [item["name"] for item in plan.get("blocked", [])],
                "error": str(error)}
    history.append(event("execution_completed", status=result["status"], backend=result.get("backend")))
    return {"execution_id": run_id, "reproducibility": reproducibility, "history": history, "route": route, "plan": plan, "result": result, "error": None}


def main() -> int:
    parser = argparse.ArgumentParser(prog="localci run")
    parser.add_argument("--root", type=pathlib.Path, default=ROOT)
    parser.add_argument("--base")
    parser.add_argument("--diff", action="store_true", help="select a conservative test subset from changed files")
    parser.add_argument("--profile", choices=("quick", "standard", "full", "delivery"), default="standard")
    parser.add_argument("--backend", choices=("auto", "host", "docker", "act", "wsl", "windows"), default="host")
    parser.add_argument("--inventory", type=pathlib.Path, default=pathlib.Path(".localci/product-commands.json"))
    parser.add_argument("--log-dir", type=pathlib.Path, help="Base directory for command logs")
    parser.add_argument("--result-file", type=pathlib.Path, help="Write the complete run result JSON to this path")
    parser.add_argument("--report-json", type=pathlib.Path, help="Write the complete shareable execution report JSON to this path")
    parser.add_argument("--report-html", type=pathlib.Path, help="Write a shareable HTML execution report to this path")
    parser.add_argument("--history-file", type=pathlib.Path, default=default_history_file(), help="Read and append compact execution history here")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-cache", action="store_true", help="Do not restore or save CI caches")
    parser.add_argument("--invalidate-cache", action="store_true", help="Discard matching caches before running")
    parser.add_argument("--max-jobs", type=int, help="Maximum concurrently running independent commands")
    parser.add_argument("--cpu-limit", type=float, help="Per-command CPU seconds on host backend")
    parser.add_argument("--memory-limit-mb", type=int, help="Per-command memory limit in MB")
    args = parser.parse_args()
    root = args.root.resolve()
    inventory = args.inventory if args.inventory.is_absolute() else root / args.inventory
    try:
        limits = {key: value for key, value in (("cpu", args.cpu_limit), ("memory_mb", args.memory_limit_mb)) if value is not None}
        payload = run(root, inventory, args.profile, args.backend, args.base,
                      args.log_dir.resolve() if args.log_dir else None,
                      args.history_file.resolve(), args.diff, not args.no_cache, args.invalidate_cache,
                      args.max_jobs, limits)
    except (OSError, ValueError, json.JSONDecodeError, KeyError) as error:
        print(f"run error: {error}", file=sys.stderr)
        return 2

    attach_report(payload)
    record_path = pathlib.Path((payload.get("result") or {}).get("log_dir") or
                               (args.history_file.parent / "records")) / "execution-record.json"
    payload["record_file"] = str(record_path)
    if args.report_json:
        payload["report_json"] = str(args.report_json.resolve())
    if args.report_html:
        payload["report_html"] = str(args.report_html.resolve())
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        plan = payload["plan"]
        print("localCI run")
        print(f"profile: {plan['profile']}")
        print(f"selected: {len(plan['selected'])}")
        if plan["blocked"]:
            print("execution: blocked (backend: none)")
            for item in plan["blocked"]:
                print(f"  - {item['name']}: missing {', '.join(item['missing'])}")
                for alternative in item["alternatives"]:
                    missing = alternative["missing"]
                    detail = "ready" if not missing else "missing " + ", ".join(missing)
                    print(f"    alternative {alternative['backend']}: {detail}")
        else:
            result = payload["result"]
            print(f"execution: {result['status']} (backend: {result.get('backend') or 'none'})")
            if result["log_dir"]:
                print(f"logs: {result['log_dir']}")
            if result["failure_summary"]:
                summary = result["failure_summary"]
                print(f"failure summary: stage={summary['stage']} command={summary['command']} "
                      f"status={summary['status']} returncode={summary['returncode']}")
            execution_summary = result["execution_summary"]
            print(f"summary: total={execution_summary['total']} success={execution_summary['success']} "
                  f"failed={execution_summary['failed']} timeout={execution_summary['timeout']} "
                  f"not_run={execution_summary['not_run']}")
            for item in result["results"]:
                duration = item.get("duration_seconds", "n/a")
                print(f"  - {item['name']}: {item['status']} ({duration}s)")
                if item["status"] in {"failed", "timeout", "cancelled"}:
                    for highlight in item.get("error_highlights", []):
                        print(f"    [{highlight['category'].upper()}] {highlight['line']}")
                    print("    error excerpt:")
                    for line in item.get("error_excerpt", "").splitlines():
                        print(f"      {line}")
    write_record(payload, record_path)
    if args.result_file:
        write_json(payload, args.result_file)
    if args.report_json:
        write_json(payload, args.report_json)
    if args.report_html:
        write_html(payload, args.report_html)
    try:
        append_record(payload, args.history_file, "run")
    except OSError as error:
        print(f"history error: {error}", file=sys.stderr)
        return 2
    return 2 if payload["error"] else (0 if payload["result"]["status"] == "success" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
