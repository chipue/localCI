#!/usr/bin/env python3
"""Dependency-free Python service preflight template."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request


def value_at(data: object, expression: str) -> object:
    value = data
    for key in expression.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=pathlib.Path)
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"python service preflight configuration failed: {error}", file=sys.stderr)
        return 2
    errors: list[str] = []
    for package in config.get("packages", []):
        module = str(package).replace("-", "_")
        if importlib.util.find_spec(module) is None:
            errors.append(f"package={package}: module not found")
    for name in config.get("env", []):
        if not os.environ.get(str(name)):
            errors.append(f"env={name}: missing")
    for target in config.get("http", []):
        url = target.get("url")
        if not url:
            errors.append("http: url is required")
            continue
        try:
            request = urllib.request.Request(url, method=target.get("method", "GET"))
            with urllib.request.urlopen(request, timeout=target.get("timeout_seconds", 5)) as response:
                body = response.read().decode("utf-8")
                expected = target.get("expect_status", 200)
                if response.status != expected:
                    errors.append(f"http={url}: status={response.status}, expected={expected}")
                    continue
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    payload = None
                for assertion in target.get("json_paths", []):
                    value = value_at(payload, str(assertion["path"]))
                    if assertion.get("not_null", True) and value is None:
                        errors.append(f"http={url}: {assertion['path']} is null or missing")
                    if value is not None and assertion.get("type") and type(value).__name__ != assertion["type"]:
                        errors.append(f"http={url}: {assertion['path']} type={type(value).__name__}, expected={assertion['type']}")
        except (OSError, urllib.error.URLError, ValueError) as error:
            errors.append(f"http={url}: {error}")
    if errors:
        print(f"python service preflight failed: {args.config}", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"python service preflight passed: {args.config}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
