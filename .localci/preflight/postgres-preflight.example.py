#!/usr/bin/env python3
"""PostgreSQL preflight using pg_isready/psql, without Python DB dependencies."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import re


def config_value(config: dict[str, object], *names: str) -> object:
    for name in names:
        if config.get(name) is not None:
            return config[name]
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=pathlib.Path)
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"PostgreSQL preflight configuration failed: {error}", file=sys.stderr)
        return 2
    if not isinstance(config, dict):
        print("PostgreSQL preflight configuration failed: root must be an object",
              file=sys.stderr)
        return 2
    if not config.get("enabled", True):
        print(f"PostgreSQL preflight skipped: {args.config}")
        return 0
    port_value = config_value(config, "pgport", "port")
    if port_value is not None:
        raw_port = str(port_value)
    else:
        raw_port = os.environ.get("PGPORT", "5432")
    try:
        port = int(raw_port)
    except ValueError:
        print("PostgreSQL preflight configuration failed: port must be an integer",
              file=sys.stderr)
        return 2
    if not 1 <= port <= 65535:
        print("PostgreSQL preflight configuration failed: port must be 1..65535",
              file=sys.stderr)
        return 2
    query = str(config.get("query", "SELECT 1")).strip()
    if not query:
        print("PostgreSQL preflight configuration failed: query must not be empty",
              file=sys.stderr)
        return 2
    missing_tools = [tool for tool in ("pg_isready", "psql") if shutil.which(tool) is None]
    if missing_tools:
        print("PostgreSQL preflight blocked: missing tool=" + ",".join(missing_tools), file=sys.stderr)
        return 2
    environment = os.environ.copy()
    config_keys = {
        "PGHOST": ("pghost", "host"),
        "PGDATABASE": ("pgdatabase", "database"),
        "PGUSER": ("pguser", "user"),
    }
    for key, aliases in config_keys.items():
        value = next((config[alias] for alias in aliases if config.get(alias) is not None), None)
        if value is not None:
            environment[key] = str(value)
    environment["PGPORT"] = str(port)
    password_env = config.get("password_env")
    if password_env is not None:
        if not isinstance(password_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", password_env):
            print("PostgreSQL preflight configuration failed: password_env is not a valid environment name",
                  file=sys.stderr)
            return 2
        if password_env not in os.environ:
            print(f"PostgreSQL preflight blocked: missing environment={password_env}",
                  file=sys.stderr)
            return 2
        environment["PGPASSWORD"] = os.environ[password_env]
    for config_name, environment_name in (("sslmode", "PGSSLMODE"),
                                          ("connect_timeout", "PGCONNECT_TIMEOUT"),
                                          ("application_name", "PGAPPNAME")):
        value = config.get(config_name)
        if value is not None:
            environment[environment_name] = str(value)
    host = environment.get("PGHOST", "127.0.0.1")
    ready = subprocess.run(["pg_isready", "-h", host, "-p", str(port)], env=environment,
                           text=True, capture_output=True)
    if ready.returncode != 0:
        print(f"PostgreSQL preflight failed: server is not ready at {host}:{port}", file=sys.stderr)
        if ready.stderr.strip():
            print(ready.stderr.strip()[-500:], file=sys.stderr)
        return 1
    checked = subprocess.run(["psql", "-X", "-v", "ON_ERROR_STOP=1", "-At", "-c", query],
                             env=environment, text=True, capture_output=True)
    if checked.returncode != 0:
        print("PostgreSQL preflight failed: query did not succeed", file=sys.stderr)
        if checked.stderr:
            print(checked.stderr.strip()[-500:], file=sys.stderr)
        return 1
    print(f"PostgreSQL preflight passed: {host}:{port}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
