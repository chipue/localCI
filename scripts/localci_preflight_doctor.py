#!/usr/bin/env python3
"""Diagnose configured preflight templates before a product CI run."""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import shutil
import subprocess
import sys
import time
from typing import Any

try:
    from .localci_preflight import TEMPLATES
except ImportError:
    from localci_preflight import TEMPLATES

ROOT = pathlib.Path(__file__).resolve().parents[1]


def config_path(root: pathlib.Path, kind: str, override: pathlib.Path | None) -> pathlib.Path:
    if override is not None:
        return override if override.is_absolute() else root / override
    return root / ".localci" / "preflight" / str(TEMPLATES[kind]["config"])


def diagnose(root: pathlib.Path, kind: str, override: pathlib.Path | None,
             timeout: int) -> dict[str, Any]:
    spec = TEMPLATES[kind]
    config = config_path(root, kind, override)
    script = root / ".localci" / "preflight" / str(spec["script"])
    result: dict[str, Any] = {
        "kind": kind,
        "config": str(config),
        "script": str(script),
        "status": "not_configured",
        "checks": {"config": False, "tools": False, "execution": False},
        "tools": [],
    }
    if not config.is_file() or not script.is_file():
        return result
    result["checks"]["config"] = True
    try:
        json.loads(config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        result["status"] = "config_error"
        result["error"] = str(error)
        return result
    missing_tools = []
    for tool in spec["tools"]:
        available = shutil.which(str(tool)) is not None
        result["tools"].append({"name": tool, "available": available})
        if not available:
            missing_tools.append(tool)
    result["checks"]["tools"] = not missing_tools
    if missing_tools:
        result["status"] = "blocked"
        result["missing_tools"] = missing_tools
        return result
    command = [spec["runner"], str(script), "--config", str(config)]
    try:
        completed = subprocess.run(command, cwd=root, text=True, capture_output=True,
                                   timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
        result["error"] = f"preflight exceeded {timeout}s"
        return result
    result["checks"]["execution"] = completed.returncode == 0
    result["exit_code"] = completed.returncode
    result["output_tail"] = (completed.stdout + completed.stderr).strip()[-1000:]
    result["status"] = "passed" if completed.returncode == 0 else (
        "blocked" if completed.returncode == 2 else "failed"
    )
    result["connectivity"] = "verified" if completed.returncode == 0 else "failed_or_unavailable"
    return result


def build_payload(root: pathlib.Path, kinds: list[str], config_override: pathlib.Path | None,
                  timeout: int) -> dict[str, Any]:
    results = [diagnose(root, kind, config_override if kind == kinds[0] and len(kinds) == 1 else None,
                        timeout) for kind in kinds]
    return {"root": str(root), "results": results,
            "summary": {"passed": sum(item["status"] == "passed" for item in results),
                        "failed": sum(item["status"] in {"failed", "timeout", "config_error"}
                                      for item in results),
                        "blocked": sum(item["status"] == "blocked" for item in results),
                        "not_configured": sum(item["status"] == "not_configured"
                                              for item in results)}}


def print_human(payload: dict[str, Any], watch: dict[str, Any] | None = None) -> None:
    if watch:
        print(f"localCI preflight doctor (watch iteration={watch['iteration']} "
              f"timestamp={watch['timestamp']})")
    else:
        print("localCI preflight doctor")
    for item in payload["results"]:
        print(f"  - {item['kind']}: {item['status']}")
        if item.get("missing_tools"):
            print(f"    missing tools: {', '.join(item['missing_tools'])}")
        if item.get("error"):
            print(f"    detail: {item['error']}")
    print("summary: " + ", ".join(f"{key}={value}"
                                 for key, value in payload["summary"].items()))


def main() -> int:
    parser = argparse.ArgumentParser(prog="localci preflight doctor")
    parser.add_argument("--root", type=pathlib.Path, default=ROOT)
    parser.add_argument("--kind", choices=sorted(TEMPLATES))
    parser.add_argument("--config", type=pathlib.Path)
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--watch", action="store_true",
                        help="repeat diagnostics until interrupted or a stop limit is reached")
    parser.add_argument("--interval", type=float, default=5.0,
                        help="seconds between watch probes (default: 5)")
    parser.add_argument("--iterations", type=int,
                        help="stop after this many watch probes")
    parser.add_argument("--duration", type=float,
                        help="stop after this many seconds of watch mode")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.config and not args.kind:
        parser.error("--kind is required with --config")
    if args.interval < 0:
        parser.error("--interval must not be negative")
    if args.iterations is not None and args.iterations <= 0:
        parser.error("--iterations must be positive")
    if args.duration is not None and args.duration <= 0:
        parser.error("--duration must be positive")
    if (args.iterations is not None or args.duration is not None) and not args.watch:
        parser.error("--iterations and --duration require --watch")
    root = args.root.resolve()
    kinds = [args.kind] if args.kind else sorted(TEMPLATES)
    if not args.watch:
        payload = build_payload(root, kinds, args.config, args.timeout)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print_human(payload)
        return 0 if payload["summary"]["failed"] == 0 else 1

    previous: dict[str, str] | None = None
    started = time.monotonic()
    iteration = 0
    last_payload: dict[str, Any] | None = None
    try:
        while args.iterations is None or iteration < args.iterations:
            if args.duration is not None and time.monotonic() - started >= args.duration:
                break
            iteration += 1
            payload = build_payload(root, kinds, args.config, args.timeout)
            states = {item["kind"]: item["status"] for item in payload["results"]}
            watch = {
                "iteration": iteration,
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "changed": previous is not None and states != previous,
            }
            payload["watch"] = watch
            last_payload = payload
            if args.json:
                print(json.dumps(payload, ensure_ascii=False), flush=True)
            else:
                print_human(payload, watch)
            previous = states
            if args.iterations is not None and iteration >= args.iterations:
                break
            if args.duration is not None:
                remaining = args.duration - (time.monotonic() - started)
                if remaining <= 0:
                    break
                time.sleep(min(args.interval, remaining))
            else:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    if last_payload is None:
        return 0
    return 0 if last_payload["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
