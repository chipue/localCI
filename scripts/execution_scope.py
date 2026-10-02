"""Per-command runtime isolation and cleanup for localCI."""
from __future__ import annotations

import os
import pathlib
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable
from typing import Any

try:
    from .security import child_environment
except ImportError:
    from security import child_environment


def _safe_token(value: str) -> str:
    return "".join(character if character.isalnum() or character in "-_" else "-"
                   for character in value).strip("-") or "run"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


class ExecutionScope:
    """Own all temporary state created for one manifest command.

    The scope is intentionally small and disposable.  Commands inherit the
    caller's environment for compatibility, while localCI-specific state is
    namespaced with a unique execution id and a private runtime directory.
    """

    def __init__(self, run_id: str | None = None, base_dir: pathlib.Path | None = None):
        self.run_id = run_id or uuid.uuid4().hex
        self.execution_id = uuid.uuid4().hex
        configured = base_dir or pathlib.Path(
            os.environ.get("LOCALCI_RUNTIME_DIR", pathlib.Path(tempfile.gettempdir()) / "localci-runs")
        )
        self.base_dir = pathlib.Path(configured).expanduser()
        self.path = self.base_dir / f"{_safe_token(self.run_id)}-{self.execution_id}"
        self._cleanups: list[Callable[[], None]] = []
        self._containers: set[str] = set()
        self._ports: set[int] = set()
        self._closed = False

    def __enter__(self) -> "ExecutionScope":
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.path.mkdir(mode=0o700)
        (self.path / "tmp").mkdir(mode=0o700)
        return self

    @property
    def environment(self) -> dict[str, str]:
        return {
            **os.environ,
            "LOCALCI_RUN_ID": self.run_id,
            "LOCALCI_EXECUTION_ID": self.execution_id,
            "LOCALCI_RUNTIME_DIR": str(self.path),
            "LOCALCI_TEMP_DIR": str(self.path / "tmp"),
            "LOCALCI_PORT_NAMESPACE": self.execution_id,
        }

    def restricted_environment(self, policy: dict[str, Any]) -> dict[str, str]:
        environment = child_environment(policy)
        environment.update({
            "LOCALCI_RUN_ID": self.run_id,
            "LOCALCI_EXECUTION_ID": self.execution_id,
            "LOCALCI_RUNTIME_DIR": str(self.path),
            "LOCALCI_TEMP_DIR": str(self.path / "tmp"),
            "LOCALCI_PORT_NAMESPACE": self.execution_id,
        })
        return environment

    def allocate_port(self) -> int:
        for _ in range(10):
            port = free_port()
            if port not in self._ports:
                self._ports.add(port)
                return port
        raise RuntimeError("could not allocate an isolated localCI port")

    def register_cleanup(self, cleanup: Callable[[], None]) -> None:
        self._cleanups.append(cleanup)

    def register_container(self, name: str) -> None:
        self._containers.add(name)

    def container_name(self, kind: str = "command") -> str:
        return f"localci-{_safe_token(kind)}-{self.execution_id[:16]}"

    def popen(self, command: str, *, cwd: pathlib.Path, env: dict[str, str],
              timeout: int, cancel_check: Callable[[], bool] | None = None,
              preexec_fn: Callable[[], None] | None = None) -> tuple[subprocess.Popen[str], str, bool]:
        """Run a command, killing its whole process group on timeout/interrupt."""
        process = subprocess.Popen(
            command, shell=True, cwd=cwd, env=env, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            start_new_session=(os.name != "nt"), preexec_fn=preexec_fn,
        )
        try:
            started = time.monotonic()
            while True:
                try:
                    output, _ = process.communicate(timeout=0.25)
                    return process, output or "", False
                except subprocess.TimeoutExpired as error:
                    if cancel_check and cancel_check():
                        self.terminate_process(process)
                        trailing, _ = process.communicate()
                        output = error.output or ""
                        if isinstance(output, bytes):
                            output = output.decode(errors="replace")
                        if isinstance(trailing, bytes):
                            trailing = trailing.decode(errors="replace")
                        return process, str(output) + str(trailing or ""), False
                    if time.monotonic() - started >= timeout:
                        raise
        except subprocess.TimeoutExpired as error:
            output = error.output or ""
            self.terminate_process(process)
            trailing, _ = process.communicate()
            if isinstance(output, bytes):
                output = output.decode(errors="replace")
            if isinstance(trailing, bytes):
                trailing = trailing.decode(errors="replace")
            return process, str(output) + str(trailing or ""), True
        except KeyboardInterrupt:
            self.terminate_process(process)
            process.communicate()
            raise

    @staticmethod
    def terminate_process(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
            else:
                process.terminate()
        except (OSError, ProcessLookupError):
            pass

    def cleanup(self) -> None:
        if self._closed:
            return
        self._closed = True
        for cleanup in reversed(self._cleanups):
            try:
                cleanup()
            except Exception:
                pass
        if self._containers and shutil.which("docker"):
            for name in sorted(self._containers):
                subprocess.run(["docker", "rm", "-f", name],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               check=False)
        shutil.rmtree(self.path, ignore_errors=True)

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.cleanup()
