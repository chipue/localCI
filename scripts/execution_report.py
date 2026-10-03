"""Build shareable JSON and HTML reports from localCI execution results."""
from __future__ import annotations

import html
import json
import pathlib
from typing import Any


STATUSES = ("success", "cached", "failed", "timeout", "interrupted", "blocked", "not_run")


def _duration(item: dict[str, Any]) -> float:
    value = item.get("duration_seconds")
    return float(value) if isinstance(value, (int, float)) else 0.0


def _stage_status(items: list[dict[str, Any]]) -> str:
    statuses = {str(item.get("status", "unknown")) for item in items}
    if statuses & {"failed", "interrupted"}:
        return "failed"
    if "timeout" in statuses:
        return "timeout"
    if "blocked" in statuses:
        return "blocked"
    if statuses and statuses <= {"success", "cached"}:
        return "success"
    if "not_run" in statuses:
        return "not_run"
    return "unknown"


def build_report(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a stable, compact view intended for humans and integrations."""
    result = payload.get("result") or {}
    plan = payload.get("plan") or (payload.get("retry_plan") or {})
    route = payload.get("route") or {}
    result_items = [item for item in result.get("results", []) if isinstance(item, dict)]
    planned_items = [item for item in plan.get("selected", []) if isinstance(item, dict)]
    blocked_items = [item for item in plan.get("blocked", []) if isinstance(item, dict)]
    stage_names: list[str] = []
    for item in planned_items + result_items + blocked_items:
        stage = str(item.get("stage", "unknown"))
        if stage not in stage_names:
            stage_names.append(stage)

    stage_stats = []
    for stage in stage_names:
        items = [item for item in result_items if str(item.get("stage", "unknown")) == stage]
        items += [
            {"name": item.get("name"), "stage": stage, "status": "blocked",
             "reason": item.get("missing") or "backend_requirements_unavailable"}
            for item in blocked_items
            if str(item.get("stage") or item.get("item", {}).get("stage", "unknown")) == stage
            and not any(existing.get("name") == item.get("name") for existing in items)
        ]
        counts = {status: sum(item.get("status") == status for item in items) for status in STATUSES}
        stage_stats.append({
            "stage": stage,
            "status": _stage_status(items),
            "duration_seconds": round(sum(_duration(item) for item in items), 3),
            "commands": len(items),
            "counts": counts,
            "items": [{"name": item.get("name"), "status": item.get("status"),
                       "duration_seconds": item.get("duration_seconds"),
                       "reason": item.get("reason")} for item in items],
        })

    failures = []
    for item in result_items:
        if item.get("status") in {"failed", "timeout", "interrupted"}:
            failures.append({
                "name": item.get("name"), "stage": item.get("stage"),
                "status": item.get("status"), "returncode": item.get("returncode"),
                "duration_seconds": item.get("duration_seconds"),
                "excerpt": item.get("error_excerpt", ""),
                "highlights": item.get("error_highlights", []),
                "log_path": item.get("log_path"),
            })
    execution_summary = result.get("execution_summary") or {}
    backend_execution = result.get("backend_execution") or {}
    actual_backend = result.get("backend") or route.get("selected") or route.get("requested")
    return {
        "schema_version": 1,
        "execution_id": payload.get("execution_id"),
        "status": result.get("status") or ("blocked" if payload.get("error") else "unknown"),
        "profile": plan.get("profile"),
        "backend": {
            "requested": route.get("requested"),
            "selected": actual_backend,
            "execution_modes": backend_execution.get("modes", {}),
            "native_commands": backend_execution.get("native_commands", 0),
            "fallback_commands": backend_execution.get("fallback_commands", 0),
        },
        "duration_seconds": round(sum(_duration(item) for item in result_items), 3),
        "stage_stats": stage_stats,
        "failure_excerpts": failures,
        "rerun_candidates": execution_summary.get("rerun_candidates", payload.get("rerun_candidates", [])),
        "summary": {key: execution_summary.get(key, 0)
                    for key in ("total", "completed", "success", "cached", "failed", "timeout", "interrupted", "blocked", "not_run")},
        "error": payload.get("error"),
    }


def attach_report(payload: dict[str, Any]) -> dict[str, Any]:
    payload["report"] = build_report(payload)
    return payload


def write_json(payload: dict[str, Any], path: pathlib.Path) -> None:
    attach_report(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _cell(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def render_html(payload: dict[str, Any]) -> str:
    report = payload.get("report") or build_report(payload)
    rows = []
    for stage in report["stage_stats"]:
        rows.append("<tr>" + "".join(f"<td>{_cell(value)}</td>" for value in (
            stage["stage"], stage["status"], stage["duration_seconds"], stage["commands"],
            ", ".join(f"{key}={value}" for key, value in stage["counts"].items() if value),
        )) + "</tr>")
    failure_sections = []
    for failure in report["failure_excerpts"]:
        excerpt = f"<pre>{_cell(failure.get('excerpt', ''))}</pre>" if failure.get("excerpt") else ""
        failure_sections.append(
            f"<details open><summary>{_cell(failure.get('stage'))} / {_cell(failure.get('name'))} "
            f"({_cell(failure.get('status'))})</summary>{excerpt}</details>"
        )
    failures = "".join(failure_sections) or "<p>なし</p>"
    candidates = report["rerun_candidates"] or []
    candidate_list = ", ".join(_cell(item) for item in candidates) or "なし"
    backend = report["backend"]
    return """<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>localCI execution report</title><style>
body{font:15px system-ui,sans-serif;line-height:1.5;margin:2rem;max-width:1100px;color:#202124}
table{border-collapse:collapse;width:100%;margin:1rem 0}th,td{border:1px solid #d7dbe0;padding:.45rem;text-align:left}th{background:#f1f3f4}
.meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:.7rem}.card{background:#f8f9fa;border-radius:6px;padding:.7rem}
pre{background:#202124;color:#f8f9fa;overflow:auto;padding:1rem;white-space:pre-wrap}details{margin:.6rem 0}
</style></head><body><h1>localCI execution report</h1>
<div class="meta"><div class="card"><b>status</b><br>__STATUS__</div><div class="card"><b>backend</b><br>__BACKEND__</div>
<div class="card"><b>duration</b><br>__DURATION__ s</div><div class="card"><b>execution id</b><br>__ID__</div></div>
<h2>Stages</h2><table><thead><tr><th>stage</th><th>status</th><th>duration (s)</th><th>commands</th><th>counts</th></tr></thead><tbody>__ROWS__</tbody></table>
<h2>Failure excerpts</h2>__FAILURES__<h2>Rerun candidates</h2><p>__CANDIDATES__</p>
<h2>Backend details</h2><p>requested: __REQUESTED__ / selected: __SELECTED__ / fallback commands: __FALLBACK__</p>
</body></html>""".replace("__STATUS__", _cell(report["status"])).replace("__BACKEND__", _cell(backend["selected"])).replace(
        "__DURATION__", _cell(report["duration_seconds"])).replace("__ID__", _cell(report["execution_id"])).replace(
        "__ROWS__", "".join(rows)).replace("__FAILURES__", failures).replace("__CANDIDATES__", candidate_list).replace(
        "__REQUESTED__", _cell(backend["requested"])).replace("__SELECTED__", _cell(backend["selected"])).replace(
        "__FALLBACK__", _cell(backend["fallback_commands"]))


def write_html(payload: dict[str, Any], path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(payload), encoding="utf-8")
