"""Collect and persist the inputs needed to reproduce a localCI run."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import platform
import shutil
import subprocess
import sys
from typing import Any


def _command_output(command: list[str], cwd: pathlib.Path | None = None) -> str | None:
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                                timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    value = (result.stdout or result.stderr).strip()
    return value or None


def _version(executable: str) -> dict[str, Any]:
    path = shutil.which(executable)
    if not path:
        return {"available": False}
    output = _command_output([path, "--version"])
    if output is None:
        output = _command_output([path, "-version"])
    return {"available": True, "path": path, "version": output}


def git_snapshot(root: pathlib.Path) -> dict[str, Any]:
    def git(*args: str) -> str | None:
        return _command_output(["git", "-C", str(root), *args])

    commit = git("rev-parse", "HEAD")
    branch = git("branch", "--show-current")
    status = git("status", "--porcelain=v1")
    diff = git("diff", "HEAD")
    return {
        "repository": git("rev-parse", "--show-toplevel"),
        "commit": commit,
        "branch": branch,
        "dirty": bool(status),
        "status": status.splitlines() if status else [],
        "working_tree_diff_sha256": hashlib.sha256((diff or "").encode()).hexdigest(),
    }


def manifest_snapshot(path: pathlib.Path) -> dict[str, Any]:
    raw = path.read_bytes()
    parsed = json.loads(raw.decode("utf-8"))
    return {
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "schema_version": parsed.get("schema_version") if isinstance(parsed, dict) else None,
        "content": parsed,
    }


def _lock_snapshot(root: pathlib.Path) -> dict[str, Any] | None:
    path = root / ".localci" / "backend.lock"
    if not path.is_file():
        return None
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip().strip("\"'")
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "values": values}


def collect(root: pathlib.Path, inventory: pathlib.Path, plan: dict[str, Any],
            route: dict[str, Any]) -> dict[str, Any]:
    requirements: set[str] = set()
    for item in plan.get("selected", []) + [entry.get("item", {}) for entry in plan.get("blocked", [])]:
        for tool in (item.get("requirements", {}) or {}).get("tools", []):
            requirements.add(str(tool))
    requirements.update({"python3", "git"})
    backend_name = str(plan.get("backend") or route.get("selected") or "host")
    if backend_name == "act":
        requirements.update({"act", "docker"})
    elif backend_name == "docker":
        requirements.add("docker")
    elif backend_name == "windows":
        requirements.add("powershell")
    elif backend_name == "wsl":
        requirements.add("wsl.exe")
    versions = {name: _version(name) for name in sorted(requirements)}
    capabilities = plan.get("capabilities", {})
    return {
        "schema_version": 1,
        "git": git_snapshot(root),
        "manifest": manifest_snapshot(inventory),
        "platform": {
            "system": platform.system(), "release": platform.release(),
            "version": platform.version(), "machine": platform.machine(),
            "python": sys.version, "python_executable": sys.executable,
            "shell": os.environ.get("SHELL"),
        },
        "backend": {
            "requested": route.get("requested"), "selected": route.get("selected"),
            "capabilities": capabilities,
            "lock": _lock_snapshot(root),
            "configured_versions": {
                key: capabilities.get(key) for key in
                ("act_version", "act_expected_version", "docker_image", "act_image", "wsl_command")
                if capabilities.get(key) is not None
            },
        },
        "tools": versions,
        "selection": {
            "reason": route.get("selection_reason"),
            "candidates": route.get("candidates", []),
        },
    }


def write_record(payload: dict[str, Any], path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
