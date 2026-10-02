#!/usr/bin/env python3
"""Run the PostgreSQL preflight watch check when a local provider is available."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from typing import Any, Callable

try:
    from .ci_history_store import append_record
except ImportError:
    from ci_history_store import append_record

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / ".localci" / "preflight" / "postgres-preflight.example.py"


def command_available(name: str, environment: dict[str, str]) -> bool:
    return shutil.which(name, path=environment.get("PATH")) is not None


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


def run(command: list[str], *, environment: dict[str, str], timeout: float = 30,
        check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, env=environment, text=True,
                            capture_output=True, timeout=timeout)
    if check and result.returncode != 0:
        raise RuntimeError((result.stdout + result.stderr).strip()[-1000:])
    return result


def find_host_tools(environment: dict[str, str]) -> dict[str, str] | None:
    candidates = [environment.get("PATH", "")]
    for prefix in ("/opt/homebrew/opt/postgresql@16/bin",
                   "/usr/local/opt/postgresql@16/bin",
                   "/opt/homebrew/opt/postgresql/bin",
                   "/usr/local/opt/postgresql/bin"):
        candidates.append(prefix)
    path = os.pathsep.join(item for item in candidates if item)
    tools = {name: shutil.which(name, path=path) for name in
             ("initdb", "pg_ctl", "pg_isready", "psql")}
    if any(value is None for value in tools.values()):
        return None
    return {name: str(value) for name, value in tools.items()}


def wait_ready(environment: dict[str, str], port: int, attempts: int = 30) -> None:
    for _ in range(attempts):
        result = subprocess.run(["pg_isready", "-h", "127.0.0.1", "-p", str(port)],
                                env={**environment, "PATH": environment["PATH"]},
                                text=True, capture_output=True)
        if result.returncode == 0:
            return
        time.sleep(0.2)
    raise RuntimeError("temporary PostgreSQL server did not become ready")


def make_product(root: pathlib.Path, port: int) -> None:
    target = root / ".localci" / "preflight"
    target.mkdir(parents=True)
    shutil.copy2(TEMPLATE, target / "postgres-preflight.py")
    (target / "postgres-preflight.json").write_text(json.dumps({
        "enabled": True,
        "host": "127.0.0.1",
        "port": port,
        "database": "postgres",
        "user": "localci",
        "query": "SELECT 1",
        "application_name": "localci-postgres-integration",
    }), encoding="utf-8")


def run_watch(root: pathlib.Path, environment: dict[str, str], port: int,
              transition: Callable[[int], None]) -> list[dict[str, Any]]:
    command = ["bash", str(ROOT / "localci"), "preflight", "doctor",
               "--root", str(root), "--kind", "postgres", "--watch",
               "--iterations", "3", "--interval", "0.3", "--json"]
    process = subprocess.Popen(command, cwd=ROOT, env=environment, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    snapshots: list[dict[str, Any]] = []
    try:
        assert process.stdout is not None
        for line in process.stdout:
            if not line.strip():
                continue
            snapshot = json.loads(line)
            snapshots.append(snapshot)
            transition(len(snapshots))
        stderr = process.stderr.read() if process.stderr else ""
        if process.wait(timeout=10) != 0:
            raise RuntimeError(f"doctor watch failed: {stderr.strip()[-1000:]}")
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
    return snapshots


def run_host(environment: dict[str, str], tools: dict[str, str]) -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="localci-postgres-integration-") as directory:
        root = pathlib.Path(directory)
        data = root / "data"
        log = root / "postgres.log"
        port = free_port()
        run([tools["initdb"], "-D", str(data), "-A", "trust", "-U", "localci"],
            environment=environment)
        def start() -> None:
            run([tools["pg_ctl"], "-D", str(data), "-o",
                 f"-h 127.0.0.1 -p {port}", "-l", str(log), "start"],
                environment=environment)
            wait_ready(environment, port)
        def stop() -> None:
            run([tools["pg_ctl"], "-D", str(data), "-m", "fast", "stop"],
                environment=environment, check=False)
        make_product(root, port)
        start()
        try:
            def transition(iteration: int) -> None:
                if iteration == 1:
                    stop()
                elif iteration == 2:
                    start()
            return run_watch(root, environment, port, transition)
        finally:
            stop()


def docker_provider(environment: dict[str, str]) -> tuple[str, str] | None:
    if not command_available("docker", environment):
        return None
    if subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL).returncode != 0:
        return None
    image = environment.get("LOCALCI_POSTGRES_IMAGE", "postgres:16-alpine")
    if subprocess.run(["docker", "image", "inspect", image],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
        return None
    return "docker", image


def make_docker_wrappers(directory: pathlib.Path, container: str) -> None:
    scripts = {
        "pg_isready": f"#!/bin/sh\nexec docker exec {container} pg_isready -h 127.0.0.1 -p 5432\n",
        "psql": f"#!/bin/sh\nexec docker exec {container} psql -U localci -d postgres \"$@\"\n",
    }
    for name, content in scripts.items():
        path = directory / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)


def run_docker(environment: dict[str, str], image: str) -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="localci-postgres-docker-") as directory:
        root = pathlib.Path(directory)
        wrapper_dir = root / "bin"
        wrapper_dir.mkdir()
        container = "localci-postgres-" + uuid.uuid4().hex[:10]
        make_docker_wrappers(wrapper_dir, container)
        docker_environment = {**environment,
                              "PATH": str(wrapper_dir) + os.pathsep + environment.get("PATH", "")}
        port = free_port()
        run(["docker", "run", "-d", "--name", container, "-e",
             "POSTGRES_USER=localci", "-e", "POSTGRES_DB=postgres",
             "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "-p",
             f"{port}:5432", image], environment=environment)
        try:
            for _ in range(40):
                if subprocess.run(["docker", "exec", container, "pg_isready", "-h",
                                   "127.0.0.1", "-p", "5432"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError("Docker PostgreSQL did not become ready")
            make_product(root, port)
            def transition(iteration: int) -> None:
                if iteration == 1:
                    run(["docker", "stop", container], environment=environment)
                elif iteration == 2:
                    run(["docker", "start", container], environment=environment)
                    for _ in range(40):
                        if subprocess.run(["docker", "exec", container, "pg_isready", "-h",
                                           "127.0.0.1", "-p", "5432"],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                            return
                        time.sleep(0.25)
                    raise RuntimeError("Docker PostgreSQL did not recover")
            return run_watch(root, docker_environment, port, transition)
        finally:
            subprocess.run(["docker", "rm", "-f", container],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> int:
    parser = argparse.ArgumentParser(prog="postgres integration check")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--provider", choices=("auto", "host", "docker"), default="auto",
                        help="provider selection (default: auto)")
    parser.add_argument("--history-file", type=pathlib.Path,
                        help="append the evaluation result to the local history store")
    args = parser.parse_args()

    def finish(payload: dict[str, Any]) -> int:
        payload.setdefault("kind", "postgres")
        if args.history_file:
            record_payload = {
                "execution_id": f"postgres-integration-{uuid.uuid4().hex}",
                "route": {"selected": payload.get("provider"),
                          "requested": args.provider, "status": payload["status"]},
                "result": {
                    "status": "success" if payload["status"] == "passed" else payload["status"],
                    "backend": payload.get("provider"),
                    "results": [{"duration_seconds": payload.get("duration_seconds", 0.0)}],
                    "execution_summary": {"total": len(payload.get("statuses", [])),
                                           "completed": len(payload.get("statuses", [])),
                                           "success": payload.get("statuses", []).count("passed"),
                                           "failed": payload.get("statuses", []).count("failed"),
                                           "not_run": 0},
                    "backend_execution": {"provider": payload.get("provider")},
                },
                "postgres_integration": payload,
            }
            append_record(record_payload, args.history_file.expanduser().resolve(),
                          "postgres_integration")
        print(json.dumps(payload, ensure_ascii=False) if args.json else
              f"PostgreSQL integration {payload['status']}: {payload.get('provider')}")
        return 0 if payload["status"] in {"passed", "skipped"} else 1

    environment = os.environ.copy()
    if not command_available("bash", environment):
        payload = {"status": "skipped", "provider": "skipped",
                   "reason": "bash is required to invoke the localci CLI"}
        return finish(payload)
    provider = "skipped"
    reason = "no Homebrew/native PostgreSQL or prepared Docker PostgreSQL image"
    snapshots: list[dict[str, Any]] = []
    try:
        tools = find_host_tools(environment) if args.provider in {"auto", "host"} else None
        if args.provider == "host" and not tools:
            payload = {"status": "skipped", "provider": "host",
                       "reason": "native PostgreSQL tools are unavailable"}
            return finish(payload)
        if tools:
            provider = "host"
            snapshots = run_host({**environment, "PATH": os.pathsep.join(
                [str(pathlib.Path(tools["pg_ctl"]).parent), environment.get("PATH", "")])}, tools)
        else:
            docker = docker_provider(environment) if args.provider in {"auto", "docker"} else None
            if docker:
                provider, image = docker
                snapshots = run_docker(environment, image)
            else:
                payload = {"status": "skipped", "provider": provider, "reason": reason}
                return finish(payload)
        statuses = [item["results"][0]["status"] for item in snapshots]
        expected = statuses == ["passed", "failed", "passed"]
        payload = {"status": "passed" if expected else "failed",
                   "provider": provider, "statuses": statuses,
                   "changed": [item["watch"]["changed"] for item in snapshots]}
    except (OSError, RuntimeError, json.JSONDecodeError) as error:
        payload = {"status": "failed", "provider": provider, "error": str(error)}
    return finish(payload)


if __name__ == "__main__":
    raise SystemExit(main())
