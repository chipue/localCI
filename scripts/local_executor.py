"""Execute only a runnable CI plan produced by the CI Router."""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import platform
import re
import shlex
import subprocess
import textwrap
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

try:
    from .execution_scope import ExecutionScope
    from .plan_gate import PlanBlockedError, ensure_runnable
except ImportError:
    from execution_scope import ExecutionScope
    from plan_gate import PlanBlockedError, ensure_runnable
try:
    from .ci_cache import restore as restore_cache, save as save_cache
except ImportError:
    from ci_cache import restore as restore_cache, save as save_cache
try:
    from .execution_control import ExclusiveResource, RunControl, exclusive_names, parse_limits
except ImportError:
    from execution_control import ExclusiveResource, RunControl, exclusive_names, parse_limits
try:
    from .security import policy_for, redact, secret_values
except ImportError:
    from security import policy_for, redact, secret_values


def _log_run_dir(log_dir: pathlib.Path) -> pathlib.Path:
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(log_dir, 0o700)
    run_dir = log_dir / f"{stamp}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    run_dir.mkdir(parents=True, exist_ok=False)
    os.chmod(run_dir, 0o700)
    return run_dir


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-") or "command"


def _tail_excerpt(output: str, max_lines: int = 20) -> str:
    lines = output.rstrip().splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    return "... (last 20 lines)\n" + "\n".join(lines[-max_lines:])


def _error_highlights(output: str, max_lines: int = 10) -> list[dict[str, str]]:
    patterns = (
        ("traceback", re.compile(r"traceback", re.IGNORECASE)),
        ("error", re.compile(r"\berror\b", re.IGNORECASE)),
        ("failed", re.compile(r"\bfailed\b|\bfailure\b", re.IGNORECASE)),
        ("warning", re.compile(r"\bwarning\b", re.IGNORECASE)),
    )
    rank = {category: index for index, (category, _) in enumerate(patterns)}
    highlights: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for line in output.splitlines():
        for category, pattern in patterns:
            if pattern.search(line):
                key = (category, line)
                if key not in seen:
                    highlights.append({"category": category, "line": line})
                    seen.add(key)
                break
    highlights.sort(key=lambda item: rank[item["category"]])
    return highlights[:max_lines]


def _failure_details(output: str) -> dict[str, Any]:
    return {
        "error_excerpt": _tail_excerpt(output),
        "error_highlights": _error_highlights(output),
    }


def _resolve_backend_command(item: dict[str, Any], backend: str) -> tuple[str, str]:
    """Resolve a product command and make backend fallback explicit.

    A manifest may provide ``backend_commands`` for commands whose invocation
    differs by runtime.  Keeping the portable shell command as a fallback
    preserves existing manifests while making the distinction observable in
    results and history.
    """
    commands = item.get("backend_commands", {})
    if isinstance(commands, dict) and isinstance(commands.get(backend), str):
        return commands[backend], "backend_override"
    command = str(item["command"])
    if backend == "host":
        return command, "native"
    if backend == "act" and command.lstrip().startswith("act "):
        return command, "act_native"
    if backend == "windows" and command.lstrip().lower().startswith(("powershell", "pwsh")):
        return command, "windows_native"
    return command, "portable_shell_fallback"


def _declared_preflight(item: dict[str, Any]) -> list[str]:
    """Turn manifest package/env requirements into runtime checks."""
    checks: list[str] = []
    requirements = item.get("requirements", {})
    for descriptor in requirements.get("packages", []):
        value = str(descriptor)
        manager, name = value.split(":", 1) if ":" in value else ("command", value)
        if manager in {"npm", "node"}:
            checks.append(f"node -e \"require.resolve({json.dumps(name)})\"")
        elif manager in {"python", "python3", "pip"}:
            module = name.replace("-", "_")
            checks.append(
                "python3 -c "
                + shlex.quote(
                    f"import importlib.util; raise SystemExit(0 if importlib.util.find_spec({module!r}) else 1)"
                )
            )
        else:
            checks.append(f"command -v {shlex.quote(name)}")
    for variable in requirements.get("env", []):
        name = str(variable)
        checks.append(f'test -n "${{{name}:-}}"')
    custom = item.get("preflight", [])
    if isinstance(custom, str):
        custom = [custom]
    return checks + [str(check) for check in custom]


def _docker_command(item: dict[str, Any], command: str, root: pathlib.Path,
                    scope: ExecutionScope, limits: dict[str, Any] | None = None,
                    environment: dict[str, str] | None = None) -> str:
    options = item.get("backend_options", {})
    docker_options = options.get("docker", {}) if isinstance(options, dict) else {}
    image = (docker_options.get("image") if isinstance(docker_options, dict) else None) or os.environ.get(
        "LOCALCI_DOCKER_IMAGE", "catthehacker/ubuntu:act-24.04"
    )
    container = scope.container_name("command")
    scope.register_container(container)
    limits = limits or {}
    resource_flags = ""
    if limits.get("cpu") is not None:
        resource_flags += f" --cpus {shlex.quote(str(limits['cpu']))}"
    if limits.get("memory_mb") is not None:
        resource_flags += f" --memory {shlex.quote(str(int(limits['memory_mb'])) + 'm')}"
    # The wrapper process itself receives the restricted environment. Passing
    # those names through keeps the same allowlist inside the container.
    env_args = "".join(f" --env {shlex.quote(name)}" for name in sorted(environment or {}))
    return (
        "docker run --rm"
        f" --name {shlex.quote(container)}"
        f" --label localci.run-id={shlex.quote(scope.run_id)}"
        f" --label localci.execution-id={shlex.quote(scope.execution_id)}"
        f" --volume {shlex.quote(str(root))}:/workspace"
        f" --volume {shlex.quote(str(scope.path))}:/localci-runtime"
        " --tmpfs /tmp"
        f"{resource_flags}"
        " --workdir /workspace"
        f" --env LOCALCI_EXECUTOR_RUN=1 --env LOCALCI_BACKEND=docker"
        f" --env LOCALCI_RUN_ID={shlex.quote(scope.run_id)}"
        f" --env LOCALCI_EXECUTION_ID={shlex.quote(scope.execution_id)}"
        " --env LOCALCI_RUNTIME_DIR=/localci-runtime"
        " --env LOCALCI_TEMP_DIR=/localci-runtime/tmp"
        f"{env_args}"
        f" {shlex.quote(str(image))} sh -lc {shlex.quote(command)}"
    )


def _resource_preexec(limits: dict[str, Any]) -> Any:
    """Apply host limits where the platform exposes POSIX rlimits."""
    if os.name == "nt" or not any(value is not None for value in limits.values()):
        return None
    try:
        import resource
    except ImportError:
        return None

    def apply_limits() -> None:
        if limits.get("cpu") is not None:
            seconds = max(1, int(float(limits["cpu"])))
            resource.setrlimit(resource.RLIMIT_CPU, (seconds, seconds))
        if limits.get("memory_mb") is not None and hasattr(resource, "RLIMIT_AS"):
            bytes_limit = max(1, int(limits["memory_mb"]) * 1024 * 1024)
            resource.setrlimit(resource.RLIMIT_AS, (bytes_limit, bytes_limit))
    return apply_limits


def _wsl_command(command: str, root: pathlib.Path) -> str:
    executable = os.environ.get("LOCALCI_WSL_COMMAND", "wsl.exe")
    return f"{shlex.quote(executable)} --cd {shlex.quote(str(root))} -- sh -lc {shlex.quote(command)}"


def _is_git_root(root: pathlib.Path) -> bool:
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def _make_act_workflow(root: pathlib.Path, name: str, command: str,
                       index: int) -> tuple[str, pathlib.Path, tuple[pathlib.Path, ...]]:
    """Create a disposable workflow that runs one manifest command in act."""
    localci_dir = root / ".localci"
    runtime_dir = localci_dir / ".runtime"
    created_dirs: list[pathlib.Path] = []
    if not localci_dir.exists():
        localci_dir.mkdir()
        created_dirs.append(localci_dir)
    if not runtime_dir.exists():
        runtime_dir.mkdir()
        created_dirs.append(runtime_dir)
    workflow = runtime_dir / f".act-{os.getpid()}-{index:02d}-{_safe_name(name)}.yml"
    indented_command = textwrap.indent(command, "          ")
    workflow.write_text(
        "name: localci-generated\n"
        "on:\n"
        "  workflow_dispatch:\n"
        "jobs:\n"
        "  localci-command:\n"
        "    runs-on: ubuntu-latest\n"
        "    env:\n"
        "      LOCALCI_EXECUTOR_RUN: '1'\n"
        "    steps:\n"
        f"      - name: {_safe_name(name)}\n"
        "        run: |\n"
        f"{indented_command}\n",
        encoding="utf-8",
    )
    configured_arch = os.environ.get("LOCALCI_ACT_ARCH")
    architecture = configured_arch or (
        "linux/arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "linux/amd64"
    )
    image = os.environ.get("LOCALCI_ACT_IMAGE", "catthehacker/ubuntu:act-24.04")
    daemon_socket = os.environ.get("LOCALCI_ACT_DAEMON_SOCKET", "-")
    act_command = (
        "act workflow_dispatch"
        f" -W {shlex.quote(str(workflow))}"
        " --bind"
        f" --container-architecture {shlex.quote(architecture)}"
        " --pull=false"
        f" --container-daemon-socket {shlex.quote(daemon_socket)}"
        f" -P ubuntu-latest={shlex.quote(image)}"
    )
    return act_command, workflow, tuple(created_dirs)


def _parallel_execute(plan: dict[str, Any], root: pathlib.Path, log_dir: pathlib.Path | None,
                      run_id: str, max_jobs: int, limits: dict[str, Any],
                      use_cache: bool, invalidate_cache: bool) -> dict[str, Any]:
    """Run dependency-ready commands concurrently, with stable result ordering."""
    selected = list(plan.get("selected", []))
    by_name = {str(item.get("name")): item for item in selected}
    pending = set(by_name)
    results: dict[str, dict[str, Any]] = {}
    backend = str(plan.get("backend", "host"))
    stopped = False
    while pending and not stopped:
        ready = [name for name in pending if all(
            str(dep) in results and results[str(dep)].get("status") in {"success", "cached"}
            for dep in by_name[name].get("depends_on", []))]
        if not ready:
            break
        with ThreadPoolExecutor(max_workers=min(max_jobs, len(ready))) as pool:
            futures = {}
            for name in ready[:max_jobs]:
                worker_plan = {**plan, "selected": [by_name[name]]}
                futures[pool.submit(execute_plan, worker_plan, root, log_dir, use_cache,
                                    invalidate_cache, 1, limits, run_id, False)] = name
            for future in as_completed(futures):
                name = futures[future]
                result = (future.result().get("results") or [{}])[0]
                results[name] = result
                pending.remove(name)
                if result.get("status") not in {"success", "cached"}:
                    stopped = True
        if stopped:
            for name in pending:
                item = by_name[name]
                results[name] = {"name": name, "stage": str(item.get("stage", "unknown")),
                                 "status": "not_run", "backend": backend, "execution_mode": "parallel",
                                 "returncode": None, "reason": "policy_stop_after_failure",
                                 "blocked_by": [], "rerunnable": bool(item.get("retryable", True))}
            pending.clear()
    ordered = [results[str(item.get("name"))] for item in selected]
    failed = next((item for item in ordered if item.get("status") in {"failed", "timeout", "interrupted", "cancelled"}), None)
    counts = {status: sum(item.get("status") == status for item in ordered)
              for status in ("success", "failed", "timeout", "interrupted", "cancelled", "not_run")}
    summary = {"total": len(selected), "completed": len(ordered) - counts["not_run"], **counts,
               "commands": [{"name": item["name"], "stage": item.get("stage"),
                              "status": item["status"], "execution_mode": item.get("execution_mode")}
                             for item in ordered],
               "rerun_candidates": [item["name"] for item in ordered if item.get("rerunnable")]}
    return {"backend": backend, "backend_execution": {"backend": backend, "modes": {"parallel": len(ordered)},
            "native_commands": 0, "fallback_commands": 0}, "log_dir": None,
            "failure_summary": ({"command": failed["name"], "stage": failed.get("stage"),
                                  "status": failed["status"], "returncode": failed.get("returncode"),
                                  "categories": [], "highlights": [], "log_path": failed.get("log_path")}
                                 if failed else None), "execution_summary": summary,
            "status": "cancelled" if any(item.get("status") == "cancelled" for item in ordered) else
                      ("success" if ordered and all(item.get("status") in {"success", "cached"} for item in ordered)
                       else "failed"), "run_id": run_id, "results": ordered}


def execute_plan(
    plan: dict[str, Any], root: pathlib.Path, log_dir: pathlib.Path | None = None,
    use_cache: bool = True, invalidate_cache: bool = False, max_jobs: int | None = None,
    limits: dict[str, Any] | None = None, run_id: str | None = None,
    _manage_control: bool = True,
) -> dict[str, Any]:
    """Run selected commands after the mandatory plan gate has passed."""
    ensure_runnable(plan)
    selected = plan.get("selected", [])
    config = plan.get("concurrency") if isinstance(plan.get("concurrency"), dict) else {}
    effective_jobs = max(1, int(max_jobs or config.get("max_jobs") or os.environ.get("LOCALCI_MAX_JOBS", "1")))
    effective_limits = limits or (plan.get("limits") if isinstance(plan.get("limits"), dict) else {})
    if not effective_limits:
        effective_limits = {key: value for key, value in (
            ("cpu", os.environ.get("LOCALCI_CPU_LIMIT")),
            ("memory_mb", os.environ.get("LOCALCI_MEMORY_LIMIT_MB")),
        ) if value not in (None, "")}
    run_id = run_id or os.environ.get("LOCALCI_RUN_ID") or uuid.uuid4().hex
    control = RunControl(run_id)
    if _manage_control:
        control.start(max_jobs=effective_jobs, limits=effective_limits)
    if effective_jobs > 1 and len(selected) > 1:
        result = _parallel_execute(plan, root, log_dir, run_id, effective_jobs, effective_limits,
                                   use_cache, invalidate_cache)
        if _manage_control:
            control.finish(result["status"], summary=result["execution_summary"])
        return result
    run_dir = _log_run_dir(log_dir or (pathlib.Path.home() / ".localci" / "logs")) if selected else None
    results: list[dict[str, Any]] = []
    backend = str(plan.get("backend", "host"))
    execution_modes: list[str] = []
    for index, item in enumerate(selected, start=1):
        name = str(item.get("name", "unnamed"))
        stage = str(item.get("stage", "unknown"))
        command, execution_mode = _resolve_backend_command(item, backend)
        cache_info = restore_cache(item, root, backend, invalidate_cache) if use_cache else {"status": "disabled"}
        if cache_info["status"] == "hit":
            execution_modes.append("cache_restore")
            results.append({"name": name, "status": "cached", "returncode": 0, "stage": stage,
                            "backend": backend, "execution_mode": "cache_restore", "cache": cache_info,
                            "rerunnable": False, "duration_seconds": 0.0, "log_path": None})
            continue
        preflight = _declared_preflight(item)
        if preflight:
            command = " && ".join(f"( {str(check)} )" for check in preflight) + f" && ( {command} )"
            execution_mode = "preflight_then_" + execution_mode
        if control.is_cancelled():
            results.append({"name": name, "status": "cancelled", "returncode": 130,
                            "stage": stage, "backend": backend, "execution_mode": execution_mode,
                            "rerunnable": True, "duration_seconds": 0.0, "log_path": None})
            break
        item_limits = parse_limits(item, effective_limits)
        locks = [ExclusiveResource(resource, run_id) for resource in exclusive_names(item)]
        if not all(lock.acquire(control) for lock in locks):
            for lock in locks:
                lock.release()
            results.append({"name": name, "status": "cancelled", "returncode": 130,
                            "stage": stage, "backend": backend, "execution_mode": execution_mode,
                            "rerunnable": True, "duration_seconds": 0.0, "log_path": None})
            break
        with ExecutionScope(run_id=run_id) as scope:
            security_policy = policy_for(item, plan.get("security"))
            generated_workflow: pathlib.Path | None = None
            generated_directories: tuple[pathlib.Path, ...] = ()
            if (backend == "act" and execution_mode == "portable_shell_fallback"
                    and _is_git_root(root)):
                command, generated_workflow, generated_directories = _make_act_workflow(
                    root, name, command, index
                )
                execution_mode = "act_workflow"
            elif backend == "docker" and execution_mode == "portable_shell_fallback":
                command = _docker_command(item, command, root, scope, item_limits,
                                          scope.restricted_environment(security_policy))
                execution_mode = "docker_container"
            elif backend == "wsl" and execution_mode == "portable_shell_fallback":
                command = _wsl_command(command, root)
                execution_mode = "wsl_native"
            execution_modes.append(execution_mode)
            timeout = int(item.get("timeout_seconds", 600))
            log_path = run_dir / f"{index:02d}-{_safe_name(name)}.log"
            started = time.monotonic()
            try:
                completed, output, timed_out = scope.popen(
                    command, cwd=root,
                    env={**scope.restricted_environment(security_policy),
                         "LOCALCI_EXECUTOR_RUN": "1", "LOCALCI_BACKEND": backend},
                    timeout=timeout,
                    cancel_check=control.is_cancelled,
                    preexec_fn=_resource_preexec(item_limits),
                )
                redacted_output = redact(output, secret_values(security_policy))
                secrets_detected = redacted_output != output
                status = "cancelled" if control.is_cancelled() else ("timeout" if timed_out else ("success" if completed.returncode == 0 else "failed"))
                log_saved = not (secrets_detected and security_policy["secret_log_policy"] == "discard")
                if log_saved:
                    log_path.write_text(redacted_output, encoding="utf-8")
                    os.chmod(log_path, 0o600)
                result = {"name": name, "status": status, "returncode": None if timed_out else completed.returncode,
                          "stage": stage, "backend": backend, "execution_mode": execution_mode,
                          "command": command,
                          "execution_id": scope.execution_id,
                          "rerunnable": status != "success" and item.get("retryable", True),
                          "duration_seconds": round(time.monotonic() - started, 3),
                          "log_path": str(log_path) if log_saved else None,
                          "secrets_detected": secrets_detected, "log_redacted": secrets_detected,
                          "log_discarded": secrets_detected and not log_saved,
                          "environment_allowlist": security_policy["env_allowlist"],
                          "cleanup": "completed", "cache": cache_info}
                if status != "success":
                    result.update(_failure_details(redacted_output))
                elif use_cache:
                    result["cache"] = {**cache_info, "save": save_cache(item, root, backend)}
            except KeyboardInterrupt:
                result = {"name": name, "status": "interrupted", "returncode": 130,
                          "stage": stage, "backend": backend, "execution_mode": execution_mode,
                          "command": command,
                          "execution_id": scope.execution_id, "rerunnable": True,
                          "duration_seconds": round(time.monotonic() - started, 3),
                          "log_path": str(log_path), "cleanup": "completed",
                          **_failure_details("localCI command interrupted")}
                log_path.write_text(redact("localCI command interrupted\n", secret_values(security_policy)), encoding="utf-8")
            finally:
                if generated_workflow is not None:
                    generated_workflow.unlink(missing_ok=True)
                for directory in reversed(generated_directories):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
        for lock in locks:
            lock.release()
        results.append(result)
        if result["status"] not in {"success", "cached"}:
            known_status = {entry["name"]: entry["status"] for entry in results}
            for skipped in selected[index:]:
                skipped_name = str(skipped.get("name", "unnamed"))
                _, skipped_mode = _resolve_backend_command(skipped, backend)
                if skipped_mode == "portable_shell_fallback" and backend == "docker":
                    skipped_mode = "docker_container"
                elif skipped_mode == "portable_shell_fallback" and backend == "wsl":
                    skipped_mode = "wsl_native"
                dependencies = [str(value) for value in skipped.get("depends_on", [])]
                failed_dependencies = [dependency for dependency in dependencies
                                       if known_status.get(dependency) in {"failed", "timeout", "interrupted"}]
                not_run_dependencies = [dependency for dependency in dependencies
                                        if known_status.get(dependency) == "not_run"]
                if failed_dependencies:
                    reason = "dependency_failed"
                    blocked_by = failed_dependencies
                elif not_run_dependencies:
                    reason = "dependency_not_run"
                    blocked_by = not_run_dependencies
                elif not skipped.get("retryable", True):
                    reason = "policy_not_retryable"
                    blocked_by = []
                else:
                    reason = "policy_stop_after_failure"
                    blocked_by = [name]
                skipped_result = {
                    "name": skipped_name,
                    "stage": str(skipped.get("stage", "unknown")),
                    "status": "not_run",
                    "backend": backend,
                    "execution_mode": skipped_mode,
                    "returncode": None,
                    "reason": reason,
                    "blocked_by": blocked_by,
                    "rerunnable": reason in {"policy_stop_after_failure", "policy_not_retryable"}
                    and skipped.get("retryable", True),
                }
                results.append(skipped_result)
                known_status[skipped_name] = "not_run"
            break
    failed_result = next((item for item in results if item["status"] in {"failed", "timeout", "interrupted", "cancelled"}), None)
    failure_summary = None
    if failed_result:
        failure_summary = {
            "command": failed_result["name"],
            "stage": failed_result["stage"],
            "status": failed_result["status"],
            "returncode": failed_result["returncode"],
            "categories": [item["category"] for item in failed_result.get("error_highlights", [])],
            "highlights": failed_result.get("error_highlights", []),
            "log_path": failed_result["log_path"],
        }
    status_counts = {status: sum(item["status"] == status for item in results)
                     for status in ("success", "cached", "failed", "timeout", "interrupted", "cancelled", "not_run")}
    execution_summary = {
        "total": len(plan.get("selected", [])),
        "completed": status_counts["success"] + status_counts["cached"] + status_counts["failed"] + status_counts["timeout"] + status_counts["interrupted"] + status_counts["cancelled"],
        **status_counts,
        "commands": [{"name": item["name"], "stage": item["stage"], "status": item["status"],
                      "execution_mode": item.get("execution_mode")}
                     for item in results],
        "rerun_candidates": [item["name"] for item in results if item.get("rerunnable")],
    }
    final_status = "cancelled" if any(item["status"] == "cancelled" for item in results) else ("success" if results and all(item["status"] in {"success", "cached"} for item in results) else ("failed" if results else "success"))
    if _manage_control:
        control.finish(final_status, summary=execution_summary)
    return {
        "backend": backend,
        "backend_execution": {
            "backend": backend,
            "modes": {mode: execution_modes.count(mode) for mode in sorted(set(execution_modes))},
            "native_commands": sum(mode in {"native", "act_native", "act_workflow",
                                             "windows_native", "docker_container", "wsl_native", "backend_override"}
                                    for mode in execution_modes),
            "fallback_commands": execution_modes.count("portable_shell_fallback"),
        },
        "log_dir": str(run_dir) if run_dir else None,
        "failure_summary": failure_summary,
        "execution_summary": execution_summary,
        "status": final_status,
        "run_id": run_id,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="local_executor")
    parser.add_argument("plan", type=pathlib.Path, help="JSON plan produced by localci plan --json")
    parser.add_argument("--root", type=pathlib.Path, default=pathlib.Path.cwd())
    parser.add_argument("--log-dir", type=pathlib.Path, help="Base directory for command logs")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--invalidate-cache", action="store_true")
    parser.add_argument("--max-jobs", type=int)
    parser.add_argument("--cpu-limit", type=float)
    parser.add_argument("--memory-limit-mb", type=int)
    args = parser.parse_args()
    try:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        limits = {key: value for key, value in (("cpu", args.cpu_limit), ("memory_mb", args.memory_limit_mb)) if value is not None}
        result = execute_plan(plan, args.root.resolve(), args.log_dir.resolve() if args.log_dir else None,
                              not args.no_cache, args.invalidate_cache, args.max_jobs, limits)
    except PlanBlockedError as error:
        print(f"executor: {error}")
        return 2
    except (OSError, json.JSONDecodeError, KeyError, ValueError) as error:
        print(f"executor error: {error}")
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"executor: {result['status']}")
        if result["failure_summary"]:
            summary = result["failure_summary"]
            print(f"failure summary: stage={summary['stage']} command={summary['command']} "
                  f"status={summary['status']} returncode={summary['returncode']}")
        summary = result["execution_summary"]
        print(f"summary: total={summary['total']} success={summary['success']} cached={summary['cached']} "
                     f"failed={summary['failed']} timeout={summary['timeout']} not_run={summary['not_run']}")
        for item in result["results"]:
            print(f"  - {item['name']}: {item['status']}")
            if item["status"] in {"failed", "timeout", "interrupted"}:
                for highlight in item.get("error_highlights", []):
                    print(f"    [{highlight['category'].upper()}] {highlight['line']}")
                print("    error excerpt:")
                for line in item.get("error_excerpt", "").splitlines():
                    print(f"      {line}")
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
