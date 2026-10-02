#!/usr/bin/env python3
"""Diagnose the local environment before a localCI run (read-only)."""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import subprocess
import sys
from typing import Any

try:
    from .backend_adapters import CORE_BACKENDS, OPTIONAL_BACKENDS, inspect_backends
    from .ci_history_store import default_history_file, read_records
    from .validate_command_manifest import load_manifest, validate
except ImportError:
    from backend_adapters import CORE_BACKENDS, OPTIONAL_BACKENDS, inspect_backends
    from ci_history_store import default_history_file, read_records
    from validate_command_manifest import load_manifest, validate

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_MIN_FREE_GB = 1.0
OK, WARN, FAIL = "ok", "warn", "fail"


def _check(name: str, status: str, summary: str, **details: Any) -> dict[str, Any]:
    return {"name": name, "status": status, "summary": summary, **details}


def _git(root: pathlib.Path, *args: str) -> tuple[int, str, str]:
    try:
        result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True,
                                timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return 127, "", str(error)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def diagnose_git(root: pathlib.Path) -> dict[str, Any]:
    code, value, error = _git(root, "rev-parse", "--is-inside-work-tree")
    if code != 0 or value != "true":
        return _check("git", WARN, "Git repository was not detected", root=str(root), error=error)
    _, branch, branch_error = _git(root, "branch", "--show-current")
    _, head, _ = _git(root, "rev-parse", "--short", "HEAD")
    _, porcelain, status_error = _git(root, "status", "--porcelain=v1")
    lines = [line for line in porcelain.splitlines() if line]
    staged = sum(len(line) >= 2 and line[0] not in {" ", "?"} for line in lines)
    unstaged = sum(len(line) >= 2 and line[1] not in {" ", "?"} for line in lines)
    untracked = sum(line.startswith("??") for line in lines)
    detached_code, detached, _ = _git(root, "symbolic-ref", "--short", "-q", "HEAD")
    detached_head = detached_code != 0 or not detached
    status = WARN if lines or detached_head else OK
    return _check("git", status, "working tree is clean" if status == OK else "working tree needs attention",
                  root=str(root), branch=branch or None, head=head or None, clean=not lines,
                  dirty=bool(lines), staged=staged, unstaged=unstaged, untracked=untracked,
                  detached_head=detached_head, error=status_error or branch_error or None,
                  hints=(["review or commit local changes before reproducing CI"] if lines else []) +
                  (["checkout a branch if branch-based policy is required"] if detached_head else []))


def diagnose_manifest(root: pathlib.Path, manifest: pathlib.Path) -> dict[str, Any]:
    if not manifest.is_file():
        return _check("manifest", WARN, "command manifest is not configured", path=str(manifest),
                      configured=False, hints=["create .localci/product-commands.json or pass --manifest"])
    errors = validate(manifest)
    try:
        data = load_manifest(manifest)
        commands = data.get("commands", [])
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return _check("manifest", FAIL, "command manifest could not be read", path=str(manifest),
                      configured=True, errors=[str(error)])
    requirements = [item.get("requirements", {}) for item in commands if isinstance(item, dict)]
    requirements = [item for item in requirements if isinstance(item, dict)]
    required_tools = sorted({str(tool) for item in requirements for tool in item.get("tools", [])})
    required_packages = sorted({str(package) for item in requirements for package in item.get("packages", [])})
    required_services = sorted({str(service) for item in requirements for service in item.get("services", [])})
    requires_docker = any(bool(item.get("docker")) for item in requirements)
    return _check("manifest", FAIL if errors else OK,
                  "command manifest is invalid" if errors else "command manifest is valid",
                  path=str(manifest), configured=True, command_count=len(commands), errors=errors,
                  requirements={"tools": required_tools, "packages": required_packages,
                                "services": required_services, "docker": requires_docker},
                  hints=["fix the reported schema errors before running CI"] if errors else [])


def diagnose_backends() -> dict[str, Any]:
    inspected = inspect_backends()
    available = [name for name, item in inspected.items() if item["available"]]
    unavailable = [name for name, item in inspected.items() if not item["available"]]
    return _check("backends", OK if "host" in available else FAIL,
                  f"{len(available)} backend(s) available", core=list(CORE_BACKENDS),
                  optional=list(OPTIONAL_BACKENDS), available_backends=available,
                  unavailable_backends=unavailable, backends=inspected,
                  hints=["install or enable a backend required by the manifest"] if unavailable else [])


def diagnose_docker() -> dict[str, Any]:
    executable = shutil.which("docker")
    if not executable:
        return _check("docker", WARN, "Docker CLI is not installed", installed=False, connected=False,
                      hints=["install Docker Desktop or another Docker runtime if required"])
    try:
        result = subprocess.run([executable, "info"], text=True, capture_output=True,
                                timeout=5, check=False)
    except subprocess.TimeoutExpired:
        return _check("docker", FAIL, "Docker daemon check timed out", installed=True, connected=False)
    except OSError as error:
        return _check("docker", FAIL, "Docker CLI could not be started", installed=True,
                      connected=False, error=str(error))
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()[-1:]
        return _check("docker", WARN, "Docker CLI is installed but daemon is unavailable",
                      installed=True, connected=False, exit_code=result.returncode, detail=detail,
                      hints=["start the Docker daemon before selecting docker or act"])
    return _check("docker", OK, "Docker daemon is reachable", installed=True, connected=True)


def diagnose_disk(root: pathlib.Path, min_free_gb: float) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(root)
    except OSError as error:
        return _check("disk", FAIL, "disk usage could not be read", path=str(root), error=str(error))
    free_gb = usage.free / (1024 ** 3)
    status = FAIL if free_gb < min_free_gb else OK
    return _check("disk", status,
                  "disk space is below the configured minimum" if status == FAIL else "disk space is sufficient",
                  path=str(root), free_bytes=usage.free, free_gb=round(free_gb, 2),
                  total_bytes=usage.total, used_bytes=usage.used, minimum_free_gb=min_free_gb,
                  hints=["free disk space or change the diagnostic threshold"] if status == FAIL else [])


def diagnose_history(history_file: pathlib.Path) -> dict[str, Any]:
    try:
        records = read_records(history_file)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return _check("ci_history", WARN, "CI history could not be read", path=str(history_file), error=str(error))
    latest = records[-1] if records else None
    failures = [item for item in records if item.get("status") in {"failed", "blocked"}]
    status = WARN if latest and latest.get("status") in {"failed", "blocked"} else OK
    summary = "latest CI run passed" if latest and status == OK else (
        "latest CI run failed or was blocked" if latest else "no CI history was recorded")
    return _check("ci_history", status, summary, path=str(history_file), records=len(records),
                  latest=latest, failures=len(failures),
                  hints=["inspect the latest result/log record and run localci doctor again"]
                  if status == WARN else [])


def make_report(root: pathlib.Path = ROOT, manifest: pathlib.Path | None = None,
                history_file: pathlib.Path | None = None, min_free_gb: float = DEFAULT_MIN_FREE_GB,
                show_all: bool = False) -> dict[str, Any]:
    # Keep the original ``make_report(show_all)`` helper signature usable for
    # callers that imported the pre-diagnostic doctor module.
    if isinstance(root, bool):
        show_all, root = root, ROOT
    root = root.resolve()
    inventory = manifest or (root / ".localci" / "product-commands.json")
    if not inventory.is_absolute():
        inventory = root / inventory
    history = history_file or default_history_file()
    checks = [diagnose_git(root), diagnose_manifest(root, inventory), diagnose_backends(),
              diagnose_docker(), diagnose_disk(root, min_free_gb), diagnose_history(history)]
    failures = [item for item in checks if item["status"] == FAIL]
    warnings = [item for item in checks if item["status"] == WARN]
    backend_check = next(item for item in checks if item["name"] == "backends")
    return {
        "root": str(root), "manifest": str(inventory), "checks": checks,
        "summary": {"status": FAIL if failures else (WARN if warnings else OK),
                     "ok": len(checks) - len(failures) - len(warnings),
                     "warnings": len(warnings), "failures": len(failures)},
        "core": list(CORE_BACKENDS), "optional": list(OPTIONAL_BACKENDS),
        "available_backends": backend_check["available_backends"],
        "unavailable_backends": backend_check["unavailable_backends"],
        "backends": backend_check["backends"] if show_all else {
            name: item for name, item in backend_check["backends"].items() if item["available"]
        },
    }


def print_human(report: dict[str, Any]) -> None:
    print("localCI doctor")
    for item in report["checks"]:
        print(f"  [{item['status']}] {item['name']}: {item['summary']}")
        for hint in item.get("hints", []):
            print(f"    hint: {hint}")
        if item["name"] == "git" and item.get("branch"):
            print(f"    branch: {item['branch']} (head {item.get('head') or 'unknown'})")
        if item["name"] == "manifest" and item.get("errors"):
            for error in item["errors"][:8]:
                print(f"    error: {error}")
    summary = report["summary"]
    print(f"summary: status={summary['status']} ok={summary['ok']} warnings={summary['warnings']} failures={summary['failures']}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="localci doctor")
    parser.add_argument("--root", type=pathlib.Path, default=ROOT)
    parser.add_argument("--manifest", type=pathlib.Path,
                        help="command manifest to validate (default: ROOT/.localci/product-commands.json)")
    parser.add_argument("--history-file", type=pathlib.Path, default=default_history_file())
    parser.add_argument("--min-free-gb", type=float, default=DEFAULT_MIN_FREE_GB,
                        help="fail when less than this much disk space is free (default: 1)")
    parser.add_argument("--all", action="store_true", help="include unavailable backend details")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.min_free_gb < 0:
        parser.error("--min-free-gb must not be negative")
    try:
        report = make_report(args.root, args.manifest, args.history_file, args.min_free_gb, args.all)
    except (OSError, ValueError, json.JSONDecodeError, KeyError) as error:
        print(f"doctor error: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_human(report)
    return 1 if report["summary"]["status"] == FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
