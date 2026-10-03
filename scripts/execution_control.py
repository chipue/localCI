"""Cancellation, resource limits, and named resource locks for localCI."""
from __future__ import annotations

import json
import os
import pathlib
import signal
import time
from typing import Any


def control_root() -> pathlib.Path:
    return pathlib.Path(os.environ.get("LOCALCI_CONTROL_DIR", pathlib.Path.home() / ".localci" / "runs"))


class RunControl:
    """A small filesystem control plane shared by a run and ``localci cancel``."""

    def __init__(self, run_id: str, root: pathlib.Path | None = None):
        self.run_id = run_id
        self.root = pathlib.Path(root or control_root())
        self.path = self.root / run_id
        self.state_path = self.path / "state.json"
        self.cancel_path = self.path / "cancel"
        self.active_path = self.path / "active.json"

    def start(self, *, max_jobs: int, limits: dict[str, Any]) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        self.write_state({"run_id": self.run_id, "status": "running", "max_jobs": max_jobs,
                          "limits": limits, "pid": os.getpid()})

    def write_state(self, state: dict[str, Any]) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.state_path)

    def set_active(self, command: str, pid: int) -> None:
        self.active_path.write_text(json.dumps({"command": command, "pid": pid}) + "\n", encoding="utf-8")

    def clear_active(self) -> None:
        self.active_path.unlink(missing_ok=True)

    def is_cancelled(self) -> bool:
        return self.cancel_path.exists()

    def finish(self, status: str, **details: Any) -> None:
        state = {"run_id": self.run_id, "status": status, **details}
        self.write_state(state)
        self.clear_active()

    def close(self) -> None:
        self.clear_active()

    def request_cancel(self) -> bool:
        self.path.mkdir(parents=True, exist_ok=True)
        self.cancel_path.write_text(str(time.time()), encoding="utf-8")
        try:
            active = json.loads(self.active_path.read_text(encoding="utf-8"))
            pid = int(active.get("pid", 0))
        except (OSError, ValueError, json.JSONDecodeError):
            pid = 0
        if pid > 0:
            try:
                os.kill(pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
        return True


class ExclusiveResource:
    """Cross-process named lock implemented with atomic directory creation."""

    def __init__(self, name: str, run_id: str, root: pathlib.Path | None = None):
        safe = "".join(char if char.isalnum() or char in "-_." else "-" for char in name).strip("-") or "resource"
        self.path = pathlib.Path(root or control_root()) / "locks" / f"{safe}.lock"
        self.run_id = run_id
        self.acquired = False

    def acquire(self, control: RunControl, timeout: float | None = None) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        while True:
            try:
                self.path.mkdir()
                (self.path / "owner").write_text(self.run_id, encoding="utf-8")
                self.acquired = True
                return True
            except FileExistsError:
                if control.is_cancelled() or (timeout is not None and time.monotonic() - started >= timeout):
                    return False
                time.sleep(0.05)

    def release(self) -> None:
        if self.acquired:
            self.acquired = False
            try:
                (self.path / "owner").unlink(missing_ok=True)
                self.path.rmdir()
            except OSError:
                pass


def parse_limits(item: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    declared = item.get("limits") if isinstance(item.get("limits"), dict) else {}
    resources = item.get("resources") if isinstance(item.get("resources"), dict) else {}
    cpu = declared.get("cpu", resources.get("cpu", defaults.get("cpu")))
    memory = declared.get("memory_mb", resources.get("memory_mb", defaults.get("memory_mb")))
    return {"cpu": cpu, "memory_mb": memory}


def exclusive_names(item: dict[str, Any]) -> list[str]:
    resources = item.get("resources") if isinstance(item.get("resources"), dict) else {}
    values = item.get("exclusive_resources", resources.get("exclusive", []))
    if isinstance(values, str):
        values = [values]
    return [str(value) for value in values or []]
