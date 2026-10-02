"""Append and query compact localCI execution history records."""
from __future__ import annotations

import argparse
import datetime as datetime_module
import json
import pathlib
import sys
from typing import Any

try:
    from .ci_history import event
except ImportError:
    from ci_history import event


def default_history_file() -> pathlib.Path:
    return pathlib.Path.home() / ".localci" / "history.jsonl"


def parse_time_bound(value: str, end: bool = False) -> datetime_module.datetime:
    """Parse an ISO timestamp or a YYYY-MM-DD date as a UTC bound."""
    try:
        parsed = datetime_module.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"invalid date/time: {value}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime_module.timezone.utc)
    if end and len(value) == 10:
        parsed += datetime_module.timedelta(days=1)
    return parsed.astimezone(datetime_module.timezone.utc)


def record_time(record: dict[str, Any]) -> datetime_module.datetime | None:
    value = record.get("timestamp")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime_module.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime_module.timezone.utc)
    return parsed.astimezone(datetime_module.timezone.utc)


def compact_record(payload: dict[str, Any], kind: str) -> dict[str, Any]:
    result = payload.get("result") or {}
    summary = result.get("execution_summary") or {}
    route = payload.get("route") or {}
    durations = [item.get("duration_seconds") for item in result.get("results", [])
                 if isinstance(item, dict) and isinstance(item.get("duration_seconds"), (int, float))]
    failure = result.get("failure_summary") or {}
    selected_backend = result.get("backend") or route.get("selected")
    reproducibility = payload.get("reproducibility") or {}
    git_snapshot = reproducibility.get("git") or {}
    manifest_snapshot = reproducibility.get("manifest") or {}
    selection = reproducibility.get("selection") or {}
    if not selected_backend:
        selected_backend = route.get("requested") if route.get("requested") in {"host", "docker", "act", "wsl", "windows"} else "unrouted"
    blocked_backends = [
        candidate.get("backend") for candidate in route.get("candidates", [])
        if candidate.get("status") == "blocked"
        or not candidate.get("execution_allowed", True)
        or not candidate.get("selected", [])
    ]
    return {
        "execution_id": payload.get("execution_id"),
        "timestamp": event("history_recorded")["timestamp"],
        "kind": kind,
        "route": route,
        "backend": selected_backend,
        "route_status": route.get("status"),
        "blocked_backends": blocked_backends,
        "status": result.get("status") or ("blocked" if payload.get("error") else "unknown"),
        "error": payload.get("error"),
        "failure_summary": result.get("failure_summary"),
        "duration_seconds": round(sum(durations), 3),
        "backend_execution": result.get("backend_execution", {}),
        "fallback_commands": (result.get("backend_execution") or {}).get("fallback_commands", 0),
        "failure_stage": failure.get("stage"),
        "failure_command": failure.get("command"),
        "execution_summary": {
            key: summary.get(key, 0)
            for key in ("total", "completed", "success", "failed", "timeout", "not_run")
        },
        "source_execution_id": next((item.get("source_execution_id")
                                     for item in reversed(payload.get("history", []))
                                     if item.get("event") == "retry_started"), None),
        "record_file": payload.get("record_file"),
        "commit": git_snapshot.get("commit"),
        "dirty": git_snapshot.get("dirty"),
        "manifest_sha256": manifest_snapshot.get("sha256"),
        "selection_reason": selection.get("reason") or route.get("selection_reason"),
        "worktree_retirement": payload.get("worktree_retirement"),
        "worktree_promotion": payload.get("worktree_promotion"),
        "postgres_integration": payload.get("postgres_integration"),
        "service_integration": payload.get("service_integration"),
        "branch": payload.get("branch"),
        "worktree": payload.get("worktree"),
    }


def append_record(payload: dict[str, Any], path: pathlib.Path, kind: str) -> dict[str, Any]:
    record = compact_record(payload, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_backend: dict[str, dict[str, Any]] = {}
    failure_stages: dict[str, int] = {}
    rechecks = {"total": 0, "success": 0, "failed": 0, "blocked": 0}
    by_branch: dict[str, dict[str, Any]] = {}
    by_worktree: dict[str, dict[str, Any]] = {}
    by_service: dict[str, dict[str, Any]] = {}

    def stats_for(container: dict[str, dict[str, Any]], key: str) -> dict[str, Any]:
        return container.setdefault(key, {
            "records": 0, "retired": 0, "promoted": 0,
            "rechecks_total": 0, "rechecks_success": 0,
            "recheck_success_rate": 0.0,
        })

    def add_rechecks(stats: dict[str, Any], checks: list[dict[str, Any]]) -> None:
        stats["rechecks_total"] += len(checks)
        stats["rechecks_success"] += sum(item.get("status") == "success" for item in checks)
    for record in records:
        backend = str(record.get("backend") or "unknown")
        stats = by_backend.setdefault(backend, {
            "runs": 0, "success": 0, "failed": 0, "cancelled": 0, "blocked": 0, "skipped": 0,
            "no_candidates": 0, "total_duration_seconds": 0.0,
            "fallback_commands": 0, "total_commands": 0,
        })
        stats["runs"] += 1
        status = record.get("status", "unknown")
        if status in stats:
            stats[status] += 1
        if isinstance(record.get("duration_seconds"), (int, float)):
            stats["total_duration_seconds"] += record["duration_seconds"]
        if isinstance(record.get("fallback_commands"), (int, float)):
            stats["fallback_commands"] += record["fallback_commands"]
        execution_summary = record.get("execution_summary") or {}
        if isinstance(execution_summary.get("total"), (int, float)):
            stats["total_commands"] += execution_summary["total"]
        integration = record.get("service_integration") or record.get("postgres_integration")
        if isinstance(integration, dict) and integration.get("kind"):
            service = str(integration["kind"])
            service_stats = by_service.setdefault(service, {
                "runs": 0, "passed": 0, "failed": 0, "skipped": 0,
                "total_duration_seconds": 0.0, "recovery_samples": 0,
                "recovery_seconds_total": 0.0,
            })
            service_stats["runs"] += 1
            status_name = "passed" if integration.get("status") == "passed" else integration.get("status", "failed")
            if status_name in service_stats:
                service_stats[status_name] += 1
            if isinstance(integration.get("duration_seconds"), (int, float)):
                service_stats["total_duration_seconds"] += integration["duration_seconds"]
            if isinstance(integration.get("recovery_seconds"), (int, float)):
                service_stats["recovery_samples"] += 1
                service_stats["recovery_seconds_total"] += integration["recovery_seconds"]
        stage = record.get("failure_stage")
        if stage:
            failure_stages[stage] = failure_stages.get(stage, 0) + 1
        retirement = record.get("worktree_retirement") or {}
        retirement_checks = retirement.get("recheck_results", [])
        for check in retirement_checks:
            rechecks["total"] += 1
            status = check.get("status")
            if status in rechecks:
                rechecks[status] += 1
        promotion = record.get("worktree_promotion") or {}
        branches = {str(item.get("branch")) for item in retirement.get("candidates", []) if item.get("branch")}
        branches.update(str(item.get("branch")) for item in retirement.get("retired", []) if item.get("branch"))
        if promotion.get("branch"):
            branches.add(str(promotion["branch"]))
        for branch in branches:
            stats = stats_for(by_branch, branch)
            stats["records"] += 1
            if record.get("kind") == "worktree_retire":
                stats["retired"] += len([item for item in retirement.get("retired", [])
                                         if item.get("branch") == branch])
            if record.get("kind") == "worktree_promote":
                stats["promoted"] += 1
            add_rechecks(stats, retirement_checks)
        worktrees = {str(item.get("path")) for item in retirement.get("candidates", []) if item.get("path")}
        worktrees.update(str(item.get("path")) for item in retirement.get("retired", []) if item.get("path"))
        if promotion.get("path"):
            worktrees.add(str(promotion["path"]))
        for worktree in worktrees:
            stats = stats_for(by_worktree, worktree)
            stats["records"] += 1
            if record.get("kind") == "worktree_retire":
                stats["retired"] += sum(item.get("path") == worktree
                                         for item in retirement.get("retired", []))
            if record.get("kind") == "worktree_promote":
                stats["promoted"] += 1
            add_rechecks(stats, retirement_checks)
    for stats in by_backend.values():
        stats["total_duration_seconds"] = round(stats["total_duration_seconds"], 3)
        stats["average_duration_seconds"] = round(
            stats["total_duration_seconds"] / stats["runs"], 3
        ) if stats["runs"] else 0.0
        stats["fallback_rate"] = round(
            stats["fallback_commands"] / stats["total_commands"], 3
        ) if stats["total_commands"] else 0.0
    rechecks["success_rate"] = round(
        rechecks["success"] / rechecks["total"], 3
    ) if rechecks["total"] else 0.0
    for collection in (by_branch, by_worktree):
        for stats in collection.values():
            stats["recheck_success_rate"] = round(
                stats["rechecks_success"] / stats["rechecks_total"], 3
            ) if stats["rechecks_total"] else 0.0
    for stats in by_service.values():
        stats["total_duration_seconds"] = round(stats["total_duration_seconds"], 3)
        stats["recovery_seconds_total"] = round(stats["recovery_seconds_total"], 3)
        stats["average_duration_seconds"] = round(
            stats["total_duration_seconds"] / stats["runs"], 3
        ) if stats["runs"] else 0.0
        stats["average_recovery_seconds"] = round(
            stats["recovery_seconds_total"] / stats["recovery_samples"], 3
        ) if stats["recovery_samples"] else 0.0
    return {"records": len(records), "by_backend": by_backend,
            "failure_stages": failure_stages, "rechecks": rechecks,
            "by_branch": by_branch, "by_worktree": by_worktree,
            "by_service": by_service}


def hierarchy_depths(path: pathlib.Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain an object")
    mapping = {str(child): str(parent) for child, parent in data.items()}
    result: dict[str, dict[str, Any]] = {"main": {"parent": None, "generation": 0}}

    def visit(branch: str, trail: set[str]) -> dict[str, Any]:
        if branch in result:
            return result[branch]
        if branch in trail:
            result[branch] = {"parent": mapping.get(branch), "generation": None, "status": "cycle"}
            return result[branch]
        parent = mapping.get(branch)
        if not parent:
            result[branch] = {"parent": None, "generation": None, "status": "unregistered"}
            return result[branch]
        parent_info = visit(parent, trail | {branch})
        generation = parent_info.get("generation")
        result[branch] = {
            "parent": parent,
            "generation": generation + 1 if generation is not None else None,
            "status": "registered" if generation is not None else parent_info.get("status", "invalid"),
        }
        return result[branch]

    for branch in mapping:
        visit(branch, set())
    return result


def matches_scope(record: dict[str, Any], branch: str | None, worktree: str | None) -> bool:
    retirement = record.get("worktree_retirement") or {}
    branch_values = {str(value) for value in (
        [record.get("branch"), record.get("formal_branch")]
        + [item.get("branch") for item in retirement.get("candidates", [])]
        + [item.get("branch") for item in retirement.get("retired", [])]
        + [item.get("branch") for item in retirement.get("recheck_results", [])]
    ) if value}
    worktree_values = {str(value) for value in (
        [record.get("worktree"), record.get("path"), record.get("formal_branch")]
        + [item.get("path") for item in retirement.get("candidates", [])]
        + [item.get("path") for item in retirement.get("retired", [])]
        + [item.get("worktree") for item in retirement.get("recheck_results", [])]
    ) if value}
    return (branch is None or branch in branch_values) and (worktree is None or worktree in worktree_values)


def read_records(path: pathlib.Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid history at line {line_number}: {error}") from error
        if isinstance(record, dict):
            records.append(record)
    return records


def main() -> int:
    parser = argparse.ArgumentParser(prog="localci history")
    parser.add_argument("--history-file", type=pathlib.Path, default=default_history_file())
    parser.add_argument("--backend", choices=("host", "docker", "act", "wsl", "windows"))
    parser.add_argument("--status", choices=("success", "failed", "blocked", "skipped", "no_candidates", "unknown"))
    parser.add_argument("--kind", choices=("run", "retry", "worktree_promote", "worktree_retire", "postgres_integration", "service_integration"))
    parser.add_argument("--branch", help="Filter by logical branch name")
    parser.add_argument("--worktree", help="Filter by worktree path or formal branch")
    parser.add_argument("--since", help="Include records at or after ISO date/time")
    parser.add_argument("--until", help="Include records before ISO date/time; dates are inclusive")
    parser.add_argument("--branch-parents-file", type=pathlib.Path,
                        help="Branch parent mapping used for hierarchy annotations")
    parser.add_argument("--limit", type=int, default=None,
                        help="Displayed record limit; summaries use all records unless set")
    parser.add_argument("--summary", action="store_true", help="Aggregate history by backend and failure stage")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        records = read_records(args.history_file)
    except (OSError, ValueError) as error:
        print(f"history error: {error}", file=sys.stderr)
        return 2
    if args.backend:
        records = [item for item in records if item.get("backend") == args.backend]
    if args.status:
        records = [item for item in records if item.get("status") == args.status]
    if args.kind:
        records = [item for item in records if item.get("kind") == args.kind]
    if args.branch or args.worktree:
        records = [item for item in records if matches_scope(item, args.branch, args.worktree)]
    try:
        since = parse_time_bound(args.since) if args.since else None
        until = parse_time_bound(args.until, end=True) if args.until else None
    except ValueError as error:
        print(f"history error: {error}", file=sys.stderr)
        return 2
    if since or until:
        records = [item for item in records
                   if (record_time(item) is not None
                       and (since is None or record_time(item) >= since)
                       and (until is None or record_time(item) < until))]
    if args.limit is not None:
        if args.limit < 0:
            parser.error("--limit must be non-negative")
        records = records[-args.limit:] if args.limit else []
    elif not args.summary:
        records = records[-20:]
    if args.summary:
        result = {"history_file": str(args.history_file), "summary": summarize(records)}
        mapping_file = args.branch_parents_file
        if mapping_file is None:
            candidate = pathlib.Path.cwd() / ".localci" / "branch-parents.json"
            mapping_file = candidate if candidate.is_file() else None
        if mapping_file:
            depths = hierarchy_depths(mapping_file)
            result["summary"]["branch_hierarchy"] = {
                branch: {**stats, **depths.get(branch, {"generation": None, "parent": None,
                                                          "status": "unregistered"})}
                for branch, stats in result["summary"]["by_branch"].items()
            }
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"localCI history summary: {args.history_file}")
            for backend, stats in result["summary"]["by_backend"].items():
                print(f"  - {backend}: runs={stats['runs']} success={stats['success']} "
                      f"failed={stats['failed']} blocked={stats['blocked']} "
                      f"avg={stats['average_duration_seconds']}s "
                      f"fallback_rate={stats['fallback_rate']}")
            if result["summary"]["failure_stages"]:
                print("failure stages:")
                for stage, count in result["summary"]["failure_stages"].items():
                    print(f"  - {stage}: {count}")
            rechecks = result["summary"]["rechecks"]
            print(f"rechecks: total={rechecks['total']} success={rechecks['success']} "
                  f"failed={rechecks['failed']} blocked={rechecks['blocked']} "
                  f"success_rate={rechecks['success_rate']}")
            for branch, stats in result["summary"].get("branch_hierarchy", {}).items():
                print(f"  - branch {branch}: gen={stats.get('generation', '?')} "
                      f"parent={stats.get('parent') or '-'} "
                      f"recheck_success_rate={stats['recheck_success_rate']}")
            for worktree, stats in result["summary"]["by_worktree"].items():
                print(f"  - worktree {worktree}: promoted={stats['promoted']} "
                      f"retired={stats['retired']} "
                      f"recheck_success_rate={stats['recheck_success_rate']}")
        return 0
    if args.json:
        print(json.dumps({"history_file": str(args.history_file), "records": records}, ensure_ascii=False, indent=2))
        return 0
    print(f"localCI history: {args.history_file}")
    for record in records:
        print(f"  - {record.get('execution_id')}: {record.get('kind')} "
              f"branch={record.get('branch') or '-'} "
              f"backend={record.get('backend')} status={record.get('status')}")
    if not records:
        print("  - none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
