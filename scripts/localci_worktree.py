#!/usr/bin/env python3
"""Promote a stable local checkout into a managed Git worktree."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from typing import Any

try:
    from .ci_history_store import default_history_file, read_records
    from .ci_history import execution_id
except ImportError:
    from ci_history_store import default_history_file, read_records
    from ci_history import execution_id

ROOT = pathlib.Path(__file__).resolve().parents[1]


def default_registry() -> pathlib.Path:
    return pathlib.Path(os.environ.get("LOCALCI_WORKTREE_REGISTRY", str(pathlib.Path.home() / ".localci" / "worktrees.json")))


def git(root: pathlib.Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(["git", *args], cwd=root, env=env, check=True,
                            text=True, capture_output=True)
    return result.stdout.strip()


def current_branch(root: pathlib.Path) -> str:
    return git(root, "branch", "--show-current")


def is_clean(root: pathlib.Path) -> bool:
    return not bool(git(root, "status", "--porcelain"))


def history_successes(history_file: pathlib.Path) -> int:
    return sum(record.get("status") == "success" for record in read_records(history_file))


def parent_from_registry(root: pathlib.Path, branch: str) -> str | None:
    path = root / ".localci" / "branch-parents.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain an object")
    parent = data.get(branch)
    return str(parent) if parent else None


def default_parent(root: pathlib.Path, branch: str) -> str | None:
    configured = parent_from_registry(root, branch)
    if configured:
        return configured
    if branch != "main" and git_ref_exists(root, "main"):
        return "main"
    return None


def git_ref_exists(root: pathlib.Path, ref: str) -> bool:
    result = subprocess.run(["git", "rev-parse", "--verify", ref], cwd=root,
                            text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0


def snapshot_commit(root: pathlib.Path, branch: str) -> tuple[str, bool]:
    """Create a temporary commit from tracked and untracked working files."""
    head = git(root, "rev-parse", "HEAD")
    if is_clean(root):
        return head, False
    with tempfile.NamedTemporaryFile(prefix="localci-index-", delete=False) as handle:
        index_path = pathlib.Path(handle.name)
    index_path.unlink(missing_ok=True)
    try:
        env = {**os.environ, "GIT_INDEX_FILE": str(index_path)}
        git(root, "read-tree", "HEAD", env=env)
        git(root, "add", "-A", env=env)
        tree = git(root, "write-tree", env=env)
        commit = git(root, "commit-tree", tree, "-p", head,
                     "-m", f"localci worktree promotion snapshot: {branch}", env=env)
        return commit, True
    finally:
        index_path.unlink(missing_ok=True)


def load_registry(path: pathlib.Path) -> dict[str, Any]:
    if not path.is_file():
        return {"worktrees": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("worktrees", []), list):
        raise ValueError(f"{path} must contain a worktrees list")
    return data


def save_registry(path: pathlib.Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def worktree_entries(root: pathlib.Path) -> list[dict[str, str | bool]]:
    """Return Git's registered worktrees, including detached entries."""
    output = git(root, "worktree", "list", "--porcelain")
    entries: list[dict[str, str | bool]] = []
    current: dict[str, str | bool] | None = None
    for line in output.splitlines():
        if line.startswith("worktree "):
            if current:
                entries.append(current)
            current = {"path": line.removeprefix("worktree ")}
        elif current and line.startswith("HEAD "):
            current["head"] = line.removeprefix("HEAD ")
        elif current and line.startswith("branch refs/heads/"):
            current["branch"] = line.removeprefix("branch refs/heads/")
        elif current and line == "detached":
            current["detached"] = True
    if current:
        entries.append(current)
    return entries


def worktree_paths(root: pathlib.Path) -> dict[str, str]:
    """Return checked-out branch names and their worktree paths."""
    return {
        str(entry["branch"]): str(entry["path"])
        for entry in worktree_entries(root)
        if entry.get("branch") and entry.get("path")
    }


def worktree_clean_state(path: pathlib.Path) -> tuple[bool | None, str]:
    """Return (clean, state) without treating a missing worktree as clean."""
    if not path.is_dir():
        return None, "missing"
    result = subprocess.run(["git", "status", "--porcelain"], cwd=path,
                            text=True, capture_output=True, check=False)
    if result.returncode != 0:
        return None, "unavailable"
    return not result.stdout.strip(), "clean" if not result.stdout.strip() else "dirty"


def worktree_diagnostics(root: pathlib.Path, registry_data: dict[str, Any],
                         paths: dict[str, str]) -> dict[str, Any]:
    """Detect dirty checkouts, duplicate registry records, and stale metadata."""
    entries = worktree_entries(root)
    dirty: list[dict[str, Any]] = []
    unavailable: list[dict[str, Any]] = []
    for entry in entries:
        path = pathlib.Path(str(entry["path"]))
        clean, state = worktree_clean_state(path)
        item = {"branch": entry.get("branch"), "path": str(path), "state": state,
                "clean": clean, "detached": bool(entry.get("detached", False))}
        if state == "dirty":
            dirty.append(item)
        elif state in {"missing", "unavailable"}:
            unavailable.append(item)

    records = [item for item in registry_data.get("worktrees", []) if isinstance(item, dict)]
    branch_counts = Counter(str(item.get("branch")) for item in records if item.get("branch"))
    formal_counts = Counter(str(item.get("formal_branch")) for item in records if item.get("formal_branch"))
    path_counts = Counter(str(item.get("path")) for item in records if item.get("path"))
    duplicate_branches = [
        {"branch": branch, "count": count}
        for branch, count in sorted(branch_counts.items()) if count > 1
    ]
    duplicate_formal_branches = [
        {"formal_branch": branch, "count": count}
        for branch, count in sorted(formal_counts.items()) if count > 1
    ]
    duplicate_paths = [
        {"path": path, "count": count}
        for path, count in sorted(path_counts.items()) if count > 1
    ]

    registered_paths = {str(item.get("path")) for item in records if item.get("path")}
    actual_paths = {str(entry.get("path")) for entry in entries if entry.get("path")}
    stale: list[dict[str, Any]] = []
    for item in records:
        branch = str(item.get("branch", ""))
        path = str(item.get("path", ""))
        formal_branch = str(item.get("formal_branch", ""))
        reasons: list[str] = []
        if not path or not pathlib.Path(path).is_dir():
            reasons.append("path_missing")
        elif path not in actual_paths:
            reasons.append("not_registered_by_git")
        if formal_branch and not git_ref_exists(root, formal_branch):
            reasons.append("formal_branch_missing")
        if reasons:
            stale.append({"branch": branch, "formal_branch": formal_branch or None,
                          "path": path or None, "reasons": reasons})
    warnings: list[str] = []
    if dirty:
        warnings.append(f"dirty worktrees: {len(dirty)}")
    if duplicate_branches:
        warnings.append(f"duplicate registry branches: {len(duplicate_branches)}")
    if duplicate_formal_branches:
        warnings.append(f"duplicate formal branches: {len(duplicate_formal_branches)}")
    if duplicate_paths:
        warnings.append(f"duplicate registry paths: {len(duplicate_paths)}")
    if stale:
        warnings.append(f"stale registry records: {len(stale)}")
    if unavailable:
        warnings.append(f"unavailable Git worktrees: {len(unavailable)}")
    return {
        "dirty_worktrees": dirty,
        "unavailable_worktrees": unavailable,
        "duplicate_branches": duplicate_branches,
        "duplicate_formal_branches": duplicate_formal_branches,
        "duplicate_paths": duplicate_paths,
        "stale_registry": stale,
        "warnings": warnings,
        "registered_paths": sorted(registered_paths),
        "git_worktree_paths": sorted(actual_paths),
    }


def branch_mapping(root: pathlib.Path) -> dict[str, str]:
    path = root / ".localci" / "branch-parents.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain an object")
    return {str(child): str(parent) for child, parent in data.items()}


def merged_into_parent(root: pathlib.Path, branch: str, parent: str) -> bool | None:
    if not git_ref_exists(root, branch) or not git_ref_exists(root, parent):
        return None
    result = subprocess.run(["git", "merge-base", "--is-ancestor", branch, parent], cwd=root)
    return result.returncode == 0


def worktree_is_clean(path: pathlib.Path) -> bool:
    if not path.is_dir():
        return False
    result = subprocess.run(["git", "status", "--porcelain"], cwd=path,
                            text=True, capture_output=True)
    return result.returncode == 0 and not result.stdout.strip()


def build_listing(root: pathlib.Path, registry: pathlib.Path) -> dict[str, Any]:
    """Calculate branch generations, placements, and merge state."""
    mapping = branch_mapping(root)
    registry_data = load_registry(registry)
    records = {
        str(item.get("branch")): item for item in registry_data.get("worktrees", [])
        if isinstance(item, dict) and item.get("branch")
    }
    paths = worktree_paths(root)
    branches = {"main", *mapping.keys(), *mapping.values(), *records.keys()}
    nodes: dict[str, dict[str, Any]] = {}
    visiting: set[str] = set()

    def calculate(branch: str) -> tuple[int | None, str]:
        if branch == "main":
            nodes.setdefault(branch, {}).update({
                "branch": branch, "parent": None, "generation": 0,
                "hierarchy_status": "root",
            })
            return 0, "root"
        if branch in nodes and nodes[branch].get("generation") is not None:
            return nodes[branch]["generation"], nodes[branch]["hierarchy_status"]
        if branch in visiting:
            return None, "cycle"
        visiting.add(branch)
        parent = mapping.get(branch)
        if not parent:
            generation, hierarchy_status = None, "unregistered"
        elif parent not in branches:
            generation, hierarchy_status = None, "missing_parent"
        else:
            parent_generation, parent_status = calculate(parent)
            generation = parent_generation + 1 if parent_generation is not None else None
            hierarchy_status = "cycle" if parent_status == "cycle" else "registered"
        visiting.discard(branch)
        node = nodes.setdefault(branch, {})
        node.update({"branch": branch, "parent": parent, "generation": generation,
                     "hierarchy_status": hierarchy_status})
        return generation, hierarchy_status

    for branch in sorted(branches):
        calculate(branch)
    for branch, node in nodes.items():
        parent = node.get("parent")
        merge_state = "root" if branch == "main" else "unmerged"
        if parent:
            merged = merged_into_parent(root, branch, parent)
            merge_state = "missing_ref" if merged is None else ("merged" if merged else "unmerged")
        record = records.get(branch, {})
        path = record.get("path") or paths.get(branch)
        clean, worktree_state = worktree_clean_state(pathlib.Path(str(path))) if path else (None, "unplaced")
        node.update({
            "path": path,
            "formal_branch": record.get("formal_branch"),
            "placement": "formal" if record.get("path") else ("checked_out" if branch in paths else "unplaced"),
            "integration_status": merge_state,
            "clean": clean,
            "worktree_state": worktree_state,
        })
    ordered = sorted(nodes.values(), key=lambda item: (item.get("generation") is None,
                                                        item.get("generation") or 0,
                                                        item["branch"]))
    return {"root": str(root), "registry": str(registry), "branches": ordered,
            "diagnostics": worktree_diagnostics(root, registry_data, paths)}


def render_tree(listing: dict[str, Any]) -> str:
    nodes = {item["branch"]: item for item in listing["branches"]}
    children: dict[str | None, list[str]] = {}
    for item in listing["branches"]:
        children.setdefault(item.get("parent"), []).append(item["branch"])
    for values in children.values():
        values.sort()
    lines = ["localCI worktree tree"]

    def visit(branch: str, prefix: str = "") -> None:
        node = nodes[branch]
        generation = node["generation"] if node["generation"] is not None else "?"
        path = f", path={node['path']}" if node.get("path") else ""
        formal = f", formal={node['formal_branch']}" if node.get("formal_branch") else ""
        state = f", state={node.get('worktree_state', 'unplaced')}"
        lines.append(f"{prefix}{branch} [gen={generation}, {node['integration_status']}, placement={node['placement']}{state}{formal}{path}]")
        descendants = children.get(branch, [])
        for index, child in enumerate(descendants):
            visit(child, prefix + ("└─ " if index == len(descendants) - 1 else "├─ "))

    roots = children.get(None, []) + (["main"] if "main" in nodes and "main" not in children.get(None, []) else [])
    roots = sorted(set(roots))
    for root_branch in roots:
        visit(root_branch)
    for branch in sorted(nodes):
        if branch not in roots and not nodes[branch].get("parent"):
            visit(branch)
    return "\n".join(lines)


def retire_candidates(root: pathlib.Path, registry: pathlib.Path,
                      listing: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    listing = listing or build_listing(root, registry)
    listing_nodes = {item["branch"]: item for item in listing["branches"]}
    mapping = branch_mapping(root)
    current_path = str(root.resolve())
    candidates = []
    for node in listing["branches"]:
        if node.get("placement") != "formal" or node.get("integration_status") != "merged":
            continue
        path = node.get("path")
        if not path or str(path) == current_path:
            continue
        path_object = pathlib.Path(path)
        formal_branch = node.get("formal_branch")
        checks = {
            "merged_into_parent": node.get("integration_status") == "merged",
            "formal_worktree_registered": node.get("placement") == "formal",
            "path_exists": path_object.is_dir(),
            "clean": worktree_is_clean(path_object),
            "not_current_checkout": str(path_object.resolve()) != current_path,
            "formal_branch_exists": bool(formal_branch and git_ref_exists(root, str(formal_branch))),
        }
        warnings = []
        rechecks = []
        parent = node.get("parent")
        if parent and parent != "main":
            upstream = mapping.get(parent)
            if upstream:
                parent_node = listing_nodes.get(parent, {})
                recheck = {
                    "branch": parent,
                    "base": upstream,
                    "worktree": parent_node.get("path"),
                    "command": f"localci merge-check --base {upstream} --profile full",
                }
                rechecks.append(recheck)
                warnings.append(
                    f"recheck {parent} against {upstream} after integrating {node['branch']}"
                )
        if not checks["clean"]:
            warnings.append("formal worktree has uncommitted changes")
        if not checks["formal_branch_exists"]:
            warnings.append("formal branch reference is missing")
        candidate = {**node, "reason": "merged_into_parent_and_formal_worktree_exists",
                     "checks": checks, "safe_to_remove": all(checks.values()),
                     "warnings": warnings, "rechecks": rechecks}
        candidates.append(candidate)
    return candidates


def run_rechecks(root: pathlib.Path, rechecks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = []
    for check in rechecks:
        path = check.get("worktree")
        if not path or not pathlib.Path(path).is_dir():
            results.append({**check, "status": "blocked", "reason": "parent worktree is not available"})
            continue
        command = ["bash", str(root / "localci"), "merge-check", "--base", check["base"], "--profile", "full"]
        completed = subprocess.run(command, cwd=path, text=True, capture_output=True)
        results.append({**check, "status": "success" if completed.returncode == 0 else "failed",
                        "exit_code": completed.returncode,
                        "output_tail": (completed.stdout + completed.stderr).strip()[-1000:]})
    return results


def record_retirement(history_file: pathlib.Path, result: dict[str, Any]) -> str | None:
    """Append a durable worktree retirement record without affecting cleanup."""
    try:
        from .ci_history_store import append_record
    except ImportError:
        from ci_history_store import append_record

    record_id = execution_id("worktree-retire")
    rechecks = result.get("recheck_results", [])
    payload = {
        "execution_id": record_id,
        "result": {
            "status": result.get("status"),
            "results": [{"name": item.get("branch"),
                         "status": "success" if item.get("branch") in {
                             retired.get("branch") for retired in result.get("retired", [])
                         } else "not_run"}
                        for item in result.get("candidates", [])],
            "execution_summary": {
                "total": len(result.get("candidates", [])),
                "completed": len(result.get("retired", [])),
                "success": len(result.get("retired", [])),
                "failed": len(result.get("skipped", [])),
                "timeout": 0,
                "not_run": 0,
            },
        },
        "worktree_retirement": {
            "status": result.get("status"),
            "candidates": result.get("candidates", []),
            "recheck_warnings": result.get("recheck_warnings", []),
            "recheck_results": rechecks,
            "retired": result.get("retired", []),
            "skipped": result.get("skipped", []),
        },
    }
    append_record(payload, history_file, "worktree_retire")
    return record_id


def record_promotion(history_file: pathlib.Path, record: dict[str, Any]) -> str:
    try:
        from .ci_history_store import append_record
    except ImportError:
        from ci_history_store import append_record
    record_id = execution_id("worktree-promote")
    append_record({
        "execution_id": record_id,
        "result": {"status": "promoted", "execution_summary": {}},
        "worktree_promotion": record,
    }, history_file, "worktree_promote")
    return record_id


def retire(root: pathlib.Path, registry: pathlib.Path, apply: bool,
           force: bool, delete_branch: bool, execute_rechecks: bool = False,
           history_file: pathlib.Path | None = None) -> dict[str, Any]:
    candidates = retire_candidates(root, registry)
    recheck_warnings = [
        {"branch": item["branch"], "warnings": item["warnings"]}
        for item in candidates if item.get("warnings")
    ]
    result: dict[str, Any] = {"status": "planned" if not apply else "retired",
                              "candidates": candidates, "recheck_warnings": recheck_warnings,
                              "retired": [], "skipped": []}

    def finalize(output: dict[str, Any]) -> dict[str, Any]:
        if history_file is not None and (apply or execute_rechecks):
            try:
                output["history_record_id"] = record_retirement(history_file, output)
            except (OSError, ValueError, TypeError) as error:
                output["history_record_error"] = str(error)
        return output

    if not apply:
        if execute_rechecks:
            result["recheck_results"] = run_rechecks(
                root, [check for item in candidates for check in item.get("rechecks", [])]
            )
        return finalize(result)
    rechecks = [check for item in candidates for check in item.get("rechecks", [])]
    if rechecks and not execute_rechecks:
        result["status"] = "recheck_required"
        result["recheck_results"] = []
        result["skipped"] = [{"branch": item["branch"], "reason": "recheck_required",
                               "rechecks": item.get("rechecks", [])}
                              for item in candidates]
        return finalize(result)
    if execute_rechecks:
        result["recheck_results"] = run_rechecks(root, rechecks)
        if any(item.get("status") != "success" for item in result["recheck_results"]):
            result["status"] = "recheck_failed"
            result["skipped"] = [{"branch": item["branch"], "reason": "recheck_failed",
                                   "rechecks": item.get("rechecks", [])}
                                  for item in candidates]
            return finalize(result)
    data = load_registry(registry)
    remaining = []
    candidate_branches = {item["branch"] for item in candidates}
    candidate_formal = {item.get("formal_branch") for item in candidates}
    for record in data.get("worktrees", []):
        branch = record.get("branch")
        if branch not in candidate_branches:
            remaining.append(record)
            continue
        candidate = next(item for item in candidates if item["branch"] == branch)
        checks = candidate.get("checks", {})
        removable = all(checks.get(key, False) for key in (
            "merged_into_parent", "formal_worktree_registered", "path_exists",
            "not_current_checkout", "formal_branch_exists",
        )) and (checks.get("clean", False) or force)
        if not removable:
            result["skipped"].append({"branch": branch, "reason": "checks_not_satisfied",
                                       "checks": checks, "warnings": candidate.get("warnings", [])})
            remaining.append(record)
            continue
        path = pathlib.Path(str(record.get("path", "")))
        formal_branch = record.get("formal_branch")
        if not path.exists():
            result["skipped"].append({"branch": branch, "reason": "worktree_path_missing"})
            remaining.append(record)
            continue
        command = ["git", "worktree", "remove"]
        if force:
            command.append("--force")
        command.append(str(path))
        removal = subprocess.run(command, cwd=root, text=True, capture_output=True)
        if removal.returncode != 0:
            result["skipped"].append({"branch": branch, "reason": "worktree_not_clean",
                                       "detail": removal.stderr.strip()})
            remaining.append(record)
            continue
        if delete_branch and formal_branch in candidate_formal:
            branch_removal = subprocess.run(["git", "branch", "-d", str(formal_branch)],
                                            cwd=root, text=True, capture_output=True)
            if branch_removal.returncode != 0:
                result["skipped"].append({"branch": branch, "reason": "formal_branch_not_deleted",
                                           "detail": branch_removal.stderr.strip()})
        result["retired"].append({"branch": branch, "formal_branch": formal_branch,
                                   "path": str(path)})
    data["worktrees"] = remaining
    save_registry(registry, data)
    return finalize(result)


def status(root: pathlib.Path, history_file: pathlib.Path, registry: pathlib.Path) -> dict[str, Any]:
    branch = current_branch(root)
    records = read_records(history_file)
    data = load_registry(registry)
    return {
        "root": str(root),
        "branch": branch,
        "clean": is_clean(root),
        "parent": parent_from_registry(root, branch),
        "history_file": str(history_file),
        "history_runs": len(records),
        "successful_runs": history_successes(history_file),
        "registered_worktrees": data.get("worktrees", []),
    }


def promote(root: pathlib.Path, history_file: pathlib.Path, registry: pathlib.Path,
            target: pathlib.Path | None, parent: str | None, min_successes: int) -> dict[str, Any]:
    branch = current_branch(root)
    if not branch or branch == "main":
        raise ValueError("main cannot be promoted to a feature worktree")
    successes = history_successes(history_file)
    if successes < min_successes:
        raise ValueError(f"promotion requires {min_successes} successful localCI runs (found {successes})")
    parent_branch = parent or default_parent(root, branch)
    if not parent_branch:
        raise ValueError("a parent branch is required before promotion")
    if not git_ref_exists(root, parent_branch):
        raise ValueError(f"parent branch does not exist: {parent_branch}")

    snapshot, temporary_commit = snapshot_commit(root, branch)
    worktree_branch = f"worktree/{branch}"
    target = target or root.parent / f"{root.name}-{branch.replace('/', '-')}-formal"
    if target.exists():
        raise ValueError(f"target worktree already exists: {target}")
    if git_ref_exists(root, worktree_branch):
        raise ValueError(f"formal branch already exists: {worktree_branch}")
    git(root, "branch", worktree_branch, snapshot)
    try:
        git(root, "worktree", "add", str(target), worktree_branch)
    except Exception:
        subprocess.run(["git", "branch", "-D", worktree_branch], cwd=root,
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        raise

    mapping_path = target / ".localci" / "branch-parents.json"
    mapping = {}
    if mapping_path.is_file():
        loaded = json.loads(mapping_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            mapping.update(loaded)
    mapping[worktree_branch] = branch
    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    mapping_path.write_text(json.dumps(dict(sorted(mapping.items())), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    subprocess.run(["bash", str(root / "scripts" / "install_local_hooks.sh")], cwd=target, check=True)

    record = {
        "branch": branch,
        "formal_branch": worktree_branch,
        "parent": parent_branch,
        "path": str(target),
        "snapshot_commit": snapshot,
        "temporary_snapshot": temporary_commit,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    data = load_registry(registry)
    data["worktrees"] = [item for item in data.get("worktrees", []) if item.get("formal_branch") != worktree_branch]
    data["worktrees"].append(record)
    save_registry(registry, data)
    result = {"status": "promoted", **record, "history_successes": successes, "source_preserved": True}
    try:
        result["history_record_id"] = record_promotion(history_file, record)
    except (OSError, ValueError, TypeError) as error:
        result["history_record_error"] = str(error)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(prog="localci worktree")
    subparsers = parser.add_subparsers(dest="action", required=True)
    for name in ("status", "list", "retire", "promote"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("--root", type=pathlib.Path, default=ROOT)
        subparser.add_argument("--history-file", type=pathlib.Path, default=default_history_file())
        subparser.add_argument("--registry", type=pathlib.Path, default=default_registry())
        subparser.add_argument("--json", action="store_true")
        if name == "promote":
            subparser.add_argument("--target", type=pathlib.Path)
            subparser.add_argument("--parent")
            subparser.add_argument("--min-successes", type=int, default=1)
        elif name == "retire":
            subparser.add_argument("--apply", action="store_true",
                                   help="Actually remove eligible worktrees; default is preview only")
            subparser.add_argument("--force", action="store_true",
                                   help="Remove dirty worktrees when used with --apply")
            subparser.add_argument("--delete-branch", action="store_true",
                                   help="Delete the formal branch after removing its worktree")
            subparser.add_argument("--run-rechecks", action="store_true",
                                   help="Run generated parent merge-checks before applying retirement")
    args = parser.parse_args()
    try:
        if args.action == "status":
            result = status(args.root.resolve(), args.history_file, args.registry)
        elif args.action == "list":
            result = build_listing(args.root.resolve(), args.registry)
            result["deletion_candidates"] = retire_candidates(
                args.root.resolve(), args.registry, result
            )
        elif args.action == "retire":
            result = retire(args.root.resolve(), args.registry, args.apply,
                            args.force, args.delete_branch, args.run_rechecks,
                            args.history_file)
        else:
            if args.min_successes < 0:
                raise ValueError("--min-successes must be non-negative")
            result = promote(args.root.resolve(), args.history_file, args.registry,
                             args.target, args.parent, args.min_successes)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"worktree {args.action} failed: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.action == "list":
        print(render_tree(result))
        diagnostics = result.get("diagnostics", {})
        for warning in diagnostics.get("warnings", []):
            print(f"warning: {warning}")
        for item in diagnostics.get("dirty_worktrees", []):
            print(f"  - dirty: {item.get('branch') or 'detached'} ({item['path']})")
        for item in diagnostics.get("stale_registry", []):
            reasons = ", ".join(item.get("reasons", []))
            print(f"  - stale: {item.get('branch')} ({reasons})")
        for item in result.get("deletion_candidates", []):
            checks = ", ".join(f"{key}={value}" for key, value in item["checks"].items())
            print(f"  - deletion candidate: {item['branch']} ({item.get('path')})")
            print(f"    checks: {checks}")
            for warning in item.get("warnings", []):
                print(f"    warning: {warning}")
    elif args.action == "retire":
        print(f"localCI worktree retire: {result['status']}")
        for item in result["candidates"]:
            checks = ", ".join(f"{key}={value}" for key, value in item["checks"].items())
            print(f"  - candidate: {item['branch']} ({item.get('path')})")
            print(f"    checks: {checks}")
            for warning in item.get("warnings", []):
                print(f"    warning: {warning}")
            for check in item.get("rechecks", []):
                print(f"    recheck command: {check['command']}"
                      f" (worktree: {check.get('worktree') or 'not found'})")
        for check in result.get("recheck_results", []):
            print(f"  - recheck: {check['branch']} -> {check['status']}")
        for item in result["retired"]:
            print(f"  - retired: {item['branch']} ({item['path']})")
        for item in result["skipped"]:
            print(f"  - skipped: {item['branch']} ({item['reason']})")
        if result["candidates"] and not args.apply:
            print("  - preview only; pass --apply to remove eligible worktrees")
    elif args.action == "status":
        print(f"localCI worktree status: {result['branch']}")
        print(f"  - clean: {result['clean']}")
        print(f"  - successful localCI runs: {result['successful_runs']}")
        print(f"  - formal worktrees: {len(result['registered_worktrees'])}")
    else:
        print(f"worktree promoted: {result['path']} ({result['formal_branch']})")
        print(f"snapshot preserved: {result['snapshot_commit']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
