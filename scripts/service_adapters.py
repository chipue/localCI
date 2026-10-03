"""Lazy external-service adapters used by localCI integration checks.

The registry contains metadata only.  Processes and containers are created by
an adapter's ``start`` method, so importing or inspecting the registry never
starts a service.
"""
from __future__ import annotations

import dataclasses
import os
import pathlib
import shutil
import socket
import subprocess
import time
import uuid
from typing import Any

try:
    from .execution_scope import ExecutionScope
except ImportError:
    from execution_scope import ExecutionScope


@dataclasses.dataclass(frozen=True)
class ServiceSpec:
    kind: str
    image: str | None
    default_port: int
    description: str


SERVICE_SPECS = {
    "http": ServiceSpec("http", None, 0, "native HTTP test server"),
    "postgres": ServiceSpec("postgres", "postgres:16-alpine", 5432, "PostgreSQL"),
    "redis": ServiceSpec("redis", "redis:7-alpine", 6379, "Redis"),
    "mysql": ServiceSpec("mysql", "mysql:8.4", 3306, "MySQL"),
}


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


def docker_ready() -> bool:
    return shutil.which("docker") is not None and subprocess.run(
        ["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


class ServiceAdapter:
    """Common lazy lifecycle contract for an external service."""

    def __init__(self, spec: ServiceSpec, root: pathlib.Path | None = None,
                 scope: ExecutionScope | None = None):
        self.spec = spec
        self.root = root
        self.scope = scope
        self.port = scope.allocate_port() if scope else free_port()
        self.started = False

    def available(self) -> tuple[bool, str]:
        raise NotImplementedError

    def start(self) -> None:
        raise NotImplementedError

    def wait_ready(self, timeout: float = 20) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def probe(self) -> dict[str, Any]:
        return {"kind": self.spec.kind, "port": self.port, "status": "ready"}


class HttpAdapter(ServiceAdapter):
    def __init__(self, root: pathlib.Path, scope: ExecutionScope | None = None):
        super().__init__(SERVICE_SPECS["http"], root, scope)
        self.process: subprocess.Popen[str] | None = None

    def available(self) -> tuple[bool, str]:
        return (shutil.which("python3") is not None,
                "python3 is required" if shutil.which("python3") is None else "")

    def start(self) -> None:
        self.process = subprocess.Popen(
            ["python3", "-m", "http.server", str(self.port), "--directory", str(self.root)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.started = True

    def wait_ready(self, timeout: float = 20) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("HTTP adapter did not become ready")

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            if self.process.pid is not None:
                try:
                    os.killpg(self.process.pid, 15)
                except (OSError, ProcessLookupError):
                    self.process.terminate()
            self.process.wait(timeout=5)
        self.started = False


class DockerAdapter(ServiceAdapter):
    def __init__(self, spec: ServiceSpec, scope: ExecutionScope | None = None):
        super().__init__(spec, scope=scope)
        suffix = scope.execution_id[:16] if scope else uuid.uuid4().hex[:10]
        self.container = f"localci-{spec.kind}-{suffix}"
        if scope:
            scope.register_container(self.container)
        self.image = os.environ.get(f"LOCALCI_{spec.kind.upper()}_IMAGE", spec.image or "")

    def available(self) -> tuple[bool, str]:
        if not docker_ready():
            return False, "Docker daemon is unavailable"
        if subprocess.run(["docker", "image", "inspect", self.image],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
            return False, f"Docker image is not prepared: {self.image}"
        return True, ""

    def start(self) -> None:
        environment = []
        if self.spec.kind == "postgres":
            environment = ["-e", "POSTGRES_USER=localci", "-e", "POSTGRES_DB=postgres",
                           "-e", "POSTGRES_HOST_AUTH_METHOD=trust"]
        elif self.spec.kind == "mysql":
            environment = ["-e", "MYSQL_ROOT_PASSWORD=localci", "-e", "MYSQL_DATABASE=localci"]
        subprocess.run(["docker", "run", "-d", "--name", self.container, *environment,
                        "-p", f"{self.port}:{self.spec.default_port}", self.image],
                       check=True, stdout=subprocess.DEVNULL)
        self.started = True

    def wait_ready(self, timeout: float = 90) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.spec.kind == "redis":
                command = ["redis-cli", "-h", "127.0.0.1", "-p", "6379", "PING"]
            elif self.spec.kind == "mysql":
                command = ["mysqladmin", "ping", "--protocol=socket", "-uroot", "-plocalci"]
            else:
                command = ["pg_isready", "-h", "127.0.0.1", "-p", "5432"]
            if subprocess.run(["docker", "exec", self.container, *command],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                return
            time.sleep(0.25)
        raise RuntimeError(f"{self.spec.kind} adapter did not become ready")

    def stop(self) -> None:
        subprocess.run(["docker", "rm", "-f", self.container],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.started = False


def adapter_for(kind: str, root: pathlib.Path | None = None,
                scope: ExecutionScope | None = None) -> ServiceAdapter:
    if kind not in SERVICE_SPECS:
        raise ValueError(f"unsupported service adapter: {kind}")
    if kind == "http":
        if root is None:
            raise ValueError("HTTP adapter requires a root directory")
        return HttpAdapter(root, scope)
    return DockerAdapter(SERVICE_SPECS[kind], scope)


def inspect_adapters() -> dict[str, dict[str, Any]]:
    """Return metadata without constructing or starting any service."""
    return {kind: {"kind": spec.kind, "image": spec.image,
                   "port": spec.default_port, "description": spec.description}
            for kind, spec in SERVICE_SPECS.items()}
