#!/usr/bin/env python3
"""Classify product commands for the Ubuntu-based act runner.

The checker intentionally uses only the Python standard library so it can run
inside the act container before product-specific dependencies are installed.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shlex
import shutil
import sys
from typing import Any


OUT_OF_SCOPE_TOOLS = {
    "brew", "codesign", "msbuild", "signtool", "xcodebuild", "xcrun",
}
OUT_OF_SCOPE_WORDS = (
    "apple sdk", "macos", "windows sdk", "visual studio", "powershell",
    "pwsh", r"\.ps1", "xcode", "codesign", "msbuild", "signtool",
)
WRAPPERS = {"env", "command", "exec", "sudo", "time", "timeout", "nice"}
SHELLS = {"bash", "sh", "zsh", "fish"}


def load_manifest(path: pathlib.Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    commands = data.get("commands") if isinstance(data, dict) else None
    if not isinstance(commands, list):
        raise ValueError("manifest must contain a commands list")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(commands, start=1):
        if not isinstance(item, dict) or not item.get("name") or not item.get("command"):
            raise ValueError(f"commands[{index}] requires name and command")
        result.append(item)
    return result


def command_tokens(command: str) -> list[str]:
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return []
    executables: list[str] = []
    expecting_env_value = False
    for token in tokens:
        if token in {"|", "&&", "||", ";", "&"}:
            expecting_env_value = False
            continue
        if token.startswith("-") or "=" in token and not token.startswith("./"):
            continue
        if expecting_env_value:
            expecting_env_value = False
            continue
        if not executables or token in SHELLS or token in WRAPPERS:
            if token == "env":
                expecting_env_value = True
                continue
            executables.append(token)
            continue
        # After the command in a shell fragment, inspect the next command
        # after a separator. Arguments are not executable requirements.
    # A lightweight scan catches commands after pipes and command separators.
    for token in tokens:
        if token in {"|", "&&", "||", ";", "&"}:
            expecting_env_value = True
        elif expecting_env_value and not token.startswith("-"):
            executables.append(token)
            expecting_env_value = False
    return executables


def basename(token: str) -> str:
    return pathlib.PurePosixPath(token).name


def classify(item: dict[str, Any]) -> dict[str, Any]:
    command = str(item["command"])
    platforms = {str(value).lower() for value in item.get("platforms", ["linux"])}
    reasons: list[str] = []
    if item.get("act") is False or "linux" not in platforms:
        reasons.append("platforms do not include linux/act")
    lowered = command.lower()
    if any(re.search(pattern, lowered) for pattern in OUT_OF_SCOPE_WORDS):
        reasons.append("contains an OS-specific command or requirement")

    tokens = command_tokens(command)
    tools = {basename(token) for token in tokens if token}
    out_of_scope_tools = sorted(tools & OUT_OF_SCOPE_TOOLS)
    if out_of_scope_tools:
        reasons.append("OS-specific tools: " + ", ".join(out_of_scope_tools))
    if reasons:
        category = "act_out_of_scope"
    else:
        missing = sorted(tool for tool in tools if shutil.which(tool) is None)
        if missing:
            category = "dependency_required"
            reasons.append("missing in current runner: " + ", ".join(missing))
        else:
            category = "ubuntu_standard"
            reasons.append("all detected executables are available")
    return {
        "name": item["name"],
        "stage": item.get("stage", "custom"),
        "command": command,
        "classification": category,
        "reason": "; ".join(reasons),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=pathlib.Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail when a command is outside act or needs an unavailable dependency",
    )
    args = parser.parse_args()
    try:
        results = [classify(item) for item in load_manifest(args.manifest)]
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"act command coverage: {error}", file=sys.stderr)
        return 2

    if args.as_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for result in results:
            print(f"{result['classification']}: {result['name']} [{result['stage']}] — {result['reason']}")
    if args.strict and any(result["classification"] != "ubuntu_standard" for result in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
