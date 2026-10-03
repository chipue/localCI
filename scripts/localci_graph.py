"""Show command dependencies and the calculated retry order."""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from typing import Any

from localci_retry import order_by_dependencies


def to_mermaid(graph: dict[str, Any]) -> str:
    """Render the dependency graph as a Mermaid flowchart."""
    names = [node["name"] for node in graph["nodes"]]
    for edge in graph["edges"]:
        if edge["from"] not in names:
            names.append(edge["from"])
        if edge["to"] not in names:
            names.append(edge["to"])
    identifiers = {name: f"n{index}" for index, name in enumerate(names)}
    node_by_name = {node["name"]: node for node in graph["nodes"]}
    lines = ["flowchart TD"]
    stages: dict[str, list[str]] = {}
    for name in names:
        node = node_by_name.get(name)
        if node:
            label = f"{name}\\n{node['stage']} / {node['status']}"
            status = str(node.get("status", "unknown")).replace("-", "_")
            class_name = f"status_{status}{'_rerun' if node['rerunnable'] else ''}"
            stage = str(node.get("stage", "unknown"))
        else:
            label = f"{name}\\nexternal dependency / unknown"
            class_name = "external"
            stage = "external"
        label = label.replace('"', "&quot;")
        stages.setdefault(stage, []).append(
            f'        {identifiers[name]}["{label}"]:::{class_name}'
        )
    for index, (stage, node_lines) in enumerate(stages.items()):
        stage_id = f"stage_{index}_{stage}"
        stage_id = re.sub(r"[^A-Za-z0-9_-]", "_", stage_id)
        stats = graph["stage_stats"].get(stage, {})
        duration = (f"{stats.get('duration_seconds', 0.0):.3f}s"
                    if stats.get("duration_available") else "unknown")
        label = (f"{stage} (time={duration}, success={stats.get('success', 0)}, "
                 f"failed={stats.get('failed', 0)}, blocked={stats.get('blocked', 0)}, timeout={stats.get('timeout', 0)}, "
                 f"not_run={stats.get('not_run', 0)})").replace('"', "&quot;")
        if (graph.get("bottleneck_stage") or {}).get("name") == stage:
            label = label[:-1] + ", BOTTLENECK]"
        lines.append(f'    subgraph {stage_id}["{label}"]')
        lines.extend(node_lines)
        lines.append("    end")
    for edge in graph["edges"]:
        lines.append(f"    {identifiers[edge['from']]} -->|{edge['label']}| {identifiers[edge['to']]}")
    lines.extend([
        "    classDef status_success fill:#d1e7dd,stroke:#198754",
        "    classDef status_failed fill:#f8d7da,stroke:#dc3545",
        "    classDef status_blocked fill:#fff3cd,stroke:#fd7e14,stroke-dasharray: 5 5",
        "    classDef status_timeout fill:#ffe5b4,stroke:#fd7e14",
        "    classDef status_not_run fill:#e2e3e5,stroke:#6c757d,stroke-dasharray: 5 5",
        "    classDef status_unknown fill:#e2d9f3,stroke:#6f42c1",
        "    classDef status_success_rerun fill:#d1e7dd,stroke:#198754,stroke-width:3px",
        "    classDef status_failed_rerun fill:#f8d7da,stroke:#dc3545,stroke-width:3px",
        "    classDef status_blocked_rerun fill:#fff3cd,stroke:#fd7e14,stroke-width:3px,stroke-dasharray: 5 5",
        "    classDef status_timeout_rerun fill:#ffe5b4,stroke:#fd7e14,stroke-width:3px",
        "    classDef status_not_run_rerun fill:#e2e3e5,stroke:#6c757d,stroke-width:3px,stroke-dasharray: 5 5",
        "    classDef status_unknown_rerun fill:#e2d9f3,stroke:#6f42c1,stroke-width:3px",
        "    classDef external fill:#f8d7da,stroke:#dc3545,stroke-dasharray: 5 5",
    ])
    return "\n".join(lines)


def make_graph(payload: dict[str, Any]) -> dict[str, Any]:
    plan = payload.get("plan")
    if not isinstance(plan, dict):
        raise ValueError("input must be a localci run JSON result containing plan")
    selected = plan.get("selected", [])
    if not isinstance(selected, list):
        raise ValueError("plan.selected must be a list")
    blocked_items = [
        item.get("item") for item in plan.get("blocked", [])
        if isinstance(item.get("item"), dict)
    ]
    selected_names = {entry.get("name") for entry in selected}
    graph_items = list(selected) + [
        item for item in blocked_items if item.get("name") not in selected_names
    ]
    result_payload = payload.get("result") or {}
    previous_results = result_payload.get("results", [])
    result_by_name = {
        item.get("name"): item
        for item in previous_results if isinstance(item, dict)
    }
    candidates = result_payload.get("execution_summary", {}).get("rerun_candidates", [])
    if not candidates:
        candidates = payload.get("rerun_candidates", [])
    if not candidates and blocked_items:
        candidates = [item.get("name") for item in blocked_items]
    if not isinstance(candidates, list) or not all(isinstance(name, str) for name in candidates):
        raise ValueError("result.execution_summary.rerun_candidates must be a list of names")
    candidate_names = set(candidates)
    nodes = []
    raw_edges = []
    stage_stats: dict[str, dict[str, Any]] = {}
    blocked_names = {item.get("name") for item in blocked_items}
    for item in graph_items:
        name = str(item.get("name", "unnamed"))
        previous = result_by_name.get(name, {})
        status = previous.get("status", "blocked" if name in blocked_names else "unknown")
        stage = item.get("stage", "unknown")
        stats = stage_stats.setdefault(stage, {
            "duration_seconds": 0.0, "duration_available": True,
            "success": 0, "failed": 0, "blocked": 0, "timeout": 0, "not_run": 0, "unknown": 0,
        })
        if isinstance(previous.get("duration_seconds"), (int, float)):
            stats["duration_seconds"] += previous["duration_seconds"]
        else:
            stats["duration_available"] = False
        stats[status if status in {"success", "failed", "blocked", "timeout", "not_run"} else "unknown"] += 1
        nodes.append({
            "name": name,
            "stage": stage,
            "status": status,
            "duration_seconds": previous.get("duration_seconds"),
            "rerunnable": name in candidate_names,
        })
        for dependency in item.get("depends_on", []):
            raw_edges.append({"from": str(dependency), "to": name})
    retry_selected = [item for item in graph_items if item.get("name") in candidate_names]
    retry_order = [item.get("name") for item in order_by_dependencies(retry_selected)]
    timed_stages = [
        (stage, stats) for stage, stats in stage_stats.items()
        if stats["duration_available"]
    ]
    bottleneck_stage = None
    if timed_stages:
        stage, stats = max(timed_stages, key=lambda entry: entry[1]["duration_seconds"])
        bottleneck_stage = {"name": stage, "duration_seconds": stats["duration_seconds"]}
    parallel_candidates: list[str] = []
    if bottleneck_stage:
        bottleneck_name = bottleneck_stage["name"]
        bottleneck_items = [item for item in graph_items if item.get("stage") == bottleneck_name]
        bottleneck_names = {str(item.get("name")) for item in bottleneck_items}
        constrained = {
            str(item.get("name"))
            for item in bottleneck_items
            if any(str(dependency) in bottleneck_names for dependency in item.get("depends_on", []))
        }
        parallel_candidates = [
            str(item.get("name")) for item in bottleneck_items
            if str(item.get("name")) not in constrained
        ]
    retry_index = {name: index for index, name in enumerate(retry_order, start=1)}
    node_by_name = {node["name"]: node for node in nodes}
    edges = []
    for edge in raw_edges:
        target = node_by_name.get(edge["to"], {})
        order = retry_index.get(edge["to"])
        duration = target.get("duration_seconds")
        order_label = f"#{order}" if order is not None else "dependency"
        duration_label = f"{duration:.3f}s" if isinstance(duration, (int, float)) else "time unknown"
        edges.append({
            **edge,
            "from_stage": node_by_name.get(edge["from"], {}).get("stage", "external"),
            "to_stage": target.get("stage", "unknown"),
            "order": order,
            "duration_seconds": duration,
            "label": f"{order_label} / {duration_label}",
        })
    return {
        "execution_id": payload.get("execution_id"),
        "history": payload.get("history", []),
        "route": payload.get("route"),
        "nodes": nodes,
        "edges": edges,
        "rerun_candidates": candidates,
        "blocked_candidates": [name for name in candidates if name in blocked_names],
        "retry_order": retry_order,
        "stage_stats": stage_stats,
        "bottleneck_stage": bottleneck_stage,
        "parallel_candidates": parallel_candidates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="localci graph")
    parser.add_argument("result", type=pathlib.Path, help="JSON result produced by localci run --json")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--mermaid", action="store_true", help="Print a Mermaid flowchart")
    args = parser.parse_args()
    try:
        payload = json.loads(args.result.read_text(encoding="utf-8"))
        graph = make_graph(payload)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        print(f"graph error: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(graph, ensure_ascii=False, indent=2))
        return 0
    if args.mermaid:
        print(to_mermaid(graph))
        return 0
    print("localCI graph")
    if graph["execution_id"]:
        print(f"execution: {graph['execution_id']}")
    if graph["history"]:
        print(f"history events: {len(graph['history'])}")
    if graph["route"]:
        route = graph["route"]
        print(f"route: requested={route.get('requested')} selected={route.get('selected')} status={route.get('status')}")
    print("nodes:")
    for node in graph["nodes"]:
        marker = " [rerun]" if node["rerunnable"] else ""
        print(f"  - {node['name']} ({node['stage']}, {node['status']}){marker}")
    print("dependencies:")
    for edge in graph["edges"]:
        print(f"  - {edge['from']} -> {edge['to']}")
    if not graph["edges"]:
        print("  - none")
    print("retry order:")
    for index, name in enumerate(graph["retry_order"], start=1):
        print(f"  {index}. {name}")
    if not graph["retry_order"]:
        print("  - none")
    if graph["bottleneck_stage"]:
        bottleneck = graph["bottleneck_stage"]
        print(f"bottleneck: {bottleneck['name']} ({bottleneck['duration_seconds']:.3f}s)")
        print("parallel candidates:")
        for name in graph["parallel_candidates"]:
            print(f"  - {name}")
        if not graph["parallel_candidates"]:
            print("  - none")
    else:
        print("bottleneck: unknown (execution time unavailable)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
