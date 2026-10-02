#!/usr/bin/env python3
"""Generic HTTP service contract preflight using only Python stdlib."""
from __future__ import annotations

import argparse
import json
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
        print(f"HTTP preflight configuration failed: {error}", file=sys.stderr)
        return 2
    errors = []
    for target in config.get("endpoints", []):
        url = target.get("url")
        if not url:
            errors.append("endpoint url is required")
            continue
        try:
            request = urllib.request.Request(
                url, method=target.get("method", "GET"),
                headers=target.get("headers", {}),
            )
            with urllib.request.urlopen(request, timeout=target.get("timeout_seconds", 5)) as response:
                body = response.read().decode("utf-8")
                expected = target.get("expect_status", 200)
                if response.status != expected:
                    errors.append(f"http={url}: status={response.status}, expected={expected}")
                    continue
                if target.get("json_paths"):
                    try:
                        payload = json.loads(body)
                    except json.JSONDecodeError:
                        errors.append(f"http={url}: response is not JSON")
                        continue
                    for assertion in target["json_paths"]:
                        value = value_at(payload, str(assertion["path"]))
                        if assertion.get("not_null", True) and value is None:
                            errors.append(f"http={url}: {assertion['path']} is null or missing")
                        expected_type = assertion.get("type")
                        if value is not None and expected_type:
                            actual_type = type(value).__name__
                            if actual_type != expected_type:
                                errors.append(f"http={url}: {assertion['path']} type={actual_type}, expected={expected_type}")
        except (OSError, urllib.error.URLError, ValueError) as error:
            errors.append(f"http={url}: {error}")
    if errors:
        print(f"HTTP service preflight failed: {args.config}", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"HTTP service preflight passed: {args.config}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
