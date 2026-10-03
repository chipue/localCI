#!/usr/bin/env python3
"""Validate the dependency-free product command manifest schema."""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shlex
import sys
from typing import Any


STAGES = {"install", "test", "typecheck", "build", "full_ci", "custom"}
STAGE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
OSES = {"linux", "macos", "windows"}
PROFILES = {"quick", "standard", "full", "delivery"}
REQUIRED_REQUIREMENTS = {"os", "docker", "services", "tools"}
WINDOWS_ONLY_TOOLS = {"powershell", "powershell.exe", "cmd", "cmd.exe"}
ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_manifest(path: pathlib.Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manifest root must be an object")
    return data


def command_executables(command: str) -> list[str]:
    """Return shell command names that must be resolvable before execution.

    This intentionally handles only command boundaries, not shell expansion. A
    manifest still owns its shell syntax, while Router can catch the common
    failure where a command's executable was simply misspelled or omitted.
    """
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return []
    boundaries = {";", "&&", "||", "|", "|&", "&"}
    executables: list[str] = []
    at_boundary = True
    for token in tokens:
        if token in boundaries:
            at_boundary = True
            continue
        if not at_boundary:
            continue
        if "=" in token and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token):
            continue
        if token in {"(", ")", "{", "}"}:
            continue
        executables.append(token)
        at_boundary = False
    return executables


def _dependency_errors(commands: list[dict[str, Any]], errors: list[str]) -> None:
    names = {item.get("name") for item in commands if isinstance(item.get("name"), str)}
    graph: dict[str, list[str]] = {name: [] for name in names}
    for index, item in enumerate(commands):
        name = item.get("name")
        if not isinstance(name, str):
            continue
        dependencies = item.get("depends_on", [])
        if not isinstance(dependencies, list):
            continue
        for dependency in dependencies:
            if isinstance(dependency, str) and dependency not in names:
                errors.append(f"commands[{index}].depends_on references unknown command: {dependency}")
            elif isinstance(dependency, str):
                graph[name].append(dependency)

    state: dict[str, int] = {}
    stack: list[str] = []
    reported: set[tuple[str, ...]] = set()

    def visit(name: str) -> None:
        state[name] = 1
        stack.append(name)
        for dependency in graph[name]:
            if state.get(dependency, 0) == 0:
                visit(dependency)
            elif state.get(dependency) == 1:
                start = stack.index(dependency)
                cycle = tuple(stack[start:] + [dependency])
                if cycle not in reported:
                    reported.add(cycle)
                    stages = [str(next(item.get("stage", "unknown") for item in commands
                                      if item.get("name") == node)) for node in cycle[:-1]]
                    errors.append(
                        "dependency cycle across stages: "
                        + " -> ".join(cycle)
                        + " (stages: " + " -> ".join(stages) + ")"
                    )
        stack.pop()
        state[name] = 2

    for name in graph:
        if state.get(name, 0) == 0:
            visit(name)


def validate(path: pathlib.Path) -> list[str]:
    try:
        data = load_manifest(path)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        return [f"cannot read manifest: {error}"]
    errors: list[str] = []
    if data.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    commands = data.get("commands")
    if not isinstance(commands, list) or not commands:
        return errors + ["commands must be a non-empty list"]
    security = data.get("security", {})
    if not isinstance(security, dict):
        errors.append("security must be an object when present")
    else:
        if security.get("secret_log_policy", "redact") not in {"redact", "discard"}:
            errors.append("security.secret_log_policy must be redact or discard")
        for field in ("env_allowlist", "secret_env"):
            values = security.get(field, [])
            if not isinstance(values, list) or any(
                    not isinstance(value, str) or not ENV_NAME_PATTERN.fullmatch(value)
                    for value in values):
                errors.append(f"security.{field} must be a list of valid variable names")
    names: set[str] = set()
    for index, item in enumerate(commands):
        prefix = f"commands[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} must be an object")
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"{prefix}.name is required")
        elif name in names:
            errors.append(f"{prefix}.name is duplicated: {name}")
        else:
            names.add(name)
        if not isinstance(item.get("command"), str) or not item["command"].strip():
            errors.append(f"{prefix}.command is required")
        stage = item.get("stage")
        if not isinstance(stage, str) or not STAGE_PATTERN.fullmatch(stage):
            errors.append(f"{prefix}.stage must be a lowercase stage name matching {STAGE_PATTERN.pattern}")
        requirements = item.get("requirements")
        if not isinstance(requirements, dict):
            errors.append(f"{prefix}.requirements is required")
            continue
        missing = REQUIRED_REQUIREMENTS - requirements.keys()
        if missing:
            errors.append(f"{prefix}.requirements missing: {', '.join(sorted(missing))}")
        os_values = requirements.get("os")
        if (not isinstance(os_values, list) or not os_values
                or not all(isinstance(value, str) for value in os_values)
                or not set(os_values) <= OSES):
            errors.append(f"{prefix}.requirements.os must contain only {sorted(OSES)}")
        if not isinstance(requirements.get("docker"), bool):
            errors.append(f"{prefix}.requirements.docker must be boolean")
        for field in ("services", "tools"):
            if not isinstance(requirements.get(field), list) or not all(isinstance(value, str) and value for value in requirements[field]):
                errors.append(f"{prefix}.requirements.{field} must be a list of strings")
        for field in ("packages", "env"):
            values = requirements.get(field, [])
            if not isinstance(values, list) or not all(isinstance(value, str) and value for value in values):
                errors.append(f"{prefix}.requirements.{field} must be a list of strings when present")
            if field == "env" and isinstance(values, list):
                for value in values:
                    if isinstance(value, str) and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
                        errors.append(f"{prefix}.requirements.env has invalid variable name: {value}")
        profiles = item.get("profiles")
        if not isinstance(profiles, list) or not profiles or not set(profiles) <= PROFILES:
            errors.append(f"{prefix}.profiles must contain only {sorted(PROFILES)}")
        timeout = item.get("timeout_seconds")
        if not isinstance(timeout, int) or timeout <= 0:
            errors.append(f"{prefix}.timeout_seconds must be a positive integer")
        paths = item.get("paths", [])
        if not isinstance(paths, list) or not all(isinstance(value, str) and value for value in paths):
            errors.append(f"{prefix}.paths must be a list of strings when present")
        if "always" in item and not isinstance(item["always"], bool):
            errors.append(f"{prefix}.always must be boolean when present")
        depends_on = item.get("depends_on", [])
        if not isinstance(depends_on, list) or not all(isinstance(value, str) and value for value in depends_on):
            errors.append(f"{prefix}.depends_on must be a list of command names when present")
        if "retryable" in item and not isinstance(item["retryable"], bool):
            errors.append(f"{prefix}.retryable must be boolean when present")
        cache = item.get("cache")
        if cache is not None and cache is not False:
            if not isinstance(cache, dict):
                errors.append(f"{prefix}.cache must be an object or false")
            else:
                for field in ("paths", "key_files"):
                    values = cache.get(field, [])
                    if not isinstance(values, list) or not all(isinstance(value, str) and value for value in values):
                        errors.append(f"{prefix}.cache.{field} must be a list of strings when present")
                if not cache.get("paths"):
                    errors.append(f"{prefix}.cache.paths must contain at least one path")
                for field in ("version", "key"):
                    if field in cache and not isinstance(cache[field], (str, int)):
                        errors.append(f"{prefix}.cache.{field} must be a string or integer when present")
        preflight = item.get("preflight", [])
        if isinstance(preflight, str):
            preflight = [preflight]
        if not isinstance(preflight, list) or not all(isinstance(value, str) and value.strip() for value in preflight):
            errors.append(f"{prefix}.preflight must be a command string or list of strings when present")
        if isinstance(requirements, dict):
            os_values = requirements.get("os", [])
            tools = requirements.get("tools", [])
            if (isinstance(os_values, list) and all(isinstance(value, str) for value in os_values)
                    and isinstance(tools, list) and all(isinstance(tool, str) for tool in tools)):
                if "windows" not in set(os_values) and any(tool in WINDOWS_ONLY_TOOLS for tool in tools):
                    errors.append(
                        f"{prefix}.requirements.tools contradict requirements.os: "
                        f"{', '.join(sorted(set(tools) & WINDOWS_ONLY_TOOLS))} requires windows"
                    )
    if isinstance(commands, list) and all(isinstance(item, dict) for item in commands):
        _dependency_errors(commands, errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=pathlib.Path)
    args = parser.parse_args()
    errors = validate(args.manifest)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 2
    print(f"command manifest valid: {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
