"""Backend capability detection used by the CI Router."""
from __future__ import annotations

import os
import platform
import pathlib
import re
import shutil
import subprocess
import sys
from typing import Any


def current_os(backend: str) -> str:
    if backend in {"act", "docker", "wsl"}:
        return "linux"
    if backend == "windows":
        return "windows"
    name = platform.system().lower()
    return {"darwin": "macos", "windows": "windows"}.get(name, "linux")


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(
            ["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=5,
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def declared_services() -> set[str]:
    values = os.environ.get("LOCALCI_SERVICES", "")
    return {value.strip().lower() for value in values.split(",") if value.strip()}


def act_architecture() -> str:
    configured = os.environ.get("LOCALCI_ACT_ARCH")
    if configured:
        return configured
    return "linux/arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "linux/amd64"


def act_image() -> str:
    return os.environ.get("LOCALCI_ACT_IMAGE", "catthehacker/ubuntu:act-24.04")


def docker_image() -> str:
    return os.environ.get("LOCALCI_DOCKER_IMAGE", "catthehacker/ubuntu:act-24.04")


def act_image_available(image: str) -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(
        ["docker", "image", "inspect", image],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def act_version_status() -> tuple[str | None, str | None, bool]:
    """Return installed/locked act versions and whether the pin is satisfied."""
    lock_path = pathlib.Path(__file__).resolve().parents[1] / ".localci" / "backend.lock"
    try:
        lock_text = lock_path.read_text(encoding="utf-8")
    except OSError:
        return None, None, False
    match = re.search(r"^version:\s*[\"']?(v?[0-9]+\.[0-9]+\.[0-9]+)[\"']?\s*$", lock_text, re.MULTILINE)
    expected = match.group(1).lstrip("v") if match else None
    executable = shutil.which("act")
    if not executable or not expected:
        return None, expected, False
    try:
        result = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=3, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, expected, False
    output = f"{result.stdout}\n{result.stderr}"
    installed_match = re.search(r"\b(?:version\s*)?v?([0-9]+\.[0-9]+\.[0-9]+)\b", output, re.IGNORECASE)
    installed = installed_match.group(1) if result.returncode == 0 and installed_match else None
    return installed, expected, installed == expected


def wsl_command() -> str | None:
    return shutil.which("wsl.exe") or shutil.which("wsl")


def wsl_tool_available(command: str | None, tool: str) -> bool:
    if not command:
        return False
    return subprocess.run(
        [command, "-e", "sh", "-lc", f"command -v {shlex_quote(tool)}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def shlex_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def package_available(descriptor: str) -> bool:
    """Check a declared runtime package without installing anything."""
    if ":" in descriptor:
        manager, name = descriptor.split(":", 1)
    else:
        manager, name = "command", descriptor
    if not name:
        return False
    if manager in {"npm", "node"}:
        if shutil.which("node") is None:
            return False
        return subprocess.run(
            ["node", "-e", f"require.resolve({name!r})"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode == 0
    if manager in {"python", "python3", "pip"}:
        module = name.replace("-", "_")
        return subprocess.run(
            [sys.executable, "-c", f"import importlib.util; raise SystemExit(0 if importlib.util.find_spec({module!r}) else 1)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode == 0
    return shutil.which(name) is not None


def detect(backend: str) -> dict[str, Any]:
    docker = docker_available()
    actual_system = platform.system().lower()
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    wsl = wsl_command()
    image = act_image()
    image_available = act_image_available(image) if backend == "act" else None
    act_version, expected_act_version, act_version_matches = (
        act_version_status() if backend == "act" else (None, None, None)
    )
    docker_runtime = backend == "docker" and docker
    wsl_runtime = backend == "wsl" and actual_system == "windows" and wsl is not None
    runtime_available = backend == "host" or (
        backend == "windows" and actual_system == "windows" and powershell is not None
    ) or (
        backend == "act" and docker and act_version_matches and image_available
    ) or docker_runtime or wsl_runtime
    tools: dict[str, bool] = {}
    packages: dict[str, bool] = {}
    if backend == "act":
        tools["act"] = bool(act_version_matches)
        tools["docker"] = docker
    if backend == "windows":
        tools["powershell"] = powershell is not None
    if backend == "docker":
        tools.update({"docker": docker, "python3": docker, "sh": docker})
    if backend == "wsl":
        tools["wsl"] = wsl_runtime
        # Do not invoke ``wsl.exe -e`` during routing. That can start a distro
        # before the Router has selected WSL. The selected WSL executor is
        # responsible for the definitive tool check when it runs the command.
        tools["python3"] = wsl_runtime
        tools["sh"] = wsl_runtime
    # Containerized backends install product dependencies during the selected
    # install stage. Host capability checks are therefore authoritative here;
    # Executor repeats package checks immediately before the command runs.
    if backend in {"host", "windows"}:
        packages = {}
    return {
        "backend": backend,
        "os": current_os(backend),
        "runtime_available": runtime_available,
        "docker": docker,
        "services": sorted(declared_services() | ({"docker"} if docker else set())),
        "tools": tools,
        "packages": packages,
        "environment": {name: bool(value) for name, value in os.environ.items()},
        "shell": powershell if backend == "windows" else shutil.which("sh"),
        "act_architecture": act_architecture() if backend == "act" else None,
        "act_image": image if backend == "act" else None,
        "act_image_available": image_available,
        "act_version": act_version,
        "act_expected_version": expected_act_version,
        "act_version_matches": act_version_matches,
        "docker_image": docker_image() if backend == "docker" else None,
        "wsl_command": wsl if backend == "wsl" else None,
    }


def missing_requirements(requirements: dict[str, Any], capabilities: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not capabilities.get("runtime_available", True):
        missing.append(f"backend runtime={capabilities['backend']}")
    supported_os = set(requirements.get("os", []))
    if supported_os and capabilities["os"] not in supported_os:
        missing.append(f"os={capabilities['os']} is not supported")
    if requirements.get("docker") and not capabilities["docker"]:
        missing.append("docker daemon")
    available_services = set(capabilities.get("services", []))
    for service in requirements.get("services", []):
        if str(service).lower() not in available_services:
            missing.append(f"service={service}")
    for tool in requirements.get("tools", []):
        name = str(tool)
        if name in capabilities.get("tools", {}):
            available = capabilities["tools"][name]
        else:
            available = shutil.which(name) is not None
        if not available:
            missing.append(f"tool={name}")
    for package in requirements.get("packages", []):
        descriptor = str(package)
        if descriptor in capabilities.get("packages", {}):
            available = capabilities["packages"][descriptor]
        elif capabilities.get("backend") in {"host", "windows"}:
            available = package_available(descriptor)
        else:
            # Docker/act/WSL dependencies are installed by the product's
            # install command and are checked again by Local Executor.
            continue
        if not available:
            missing.append(f"package={descriptor}")
    for variable in requirements.get("env", []):
        name = str(variable)
        if not os.environ.get(name):
            missing.append(f"env={name}")
    return missing
