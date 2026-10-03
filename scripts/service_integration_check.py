#!/usr/bin/env python3
"""Run an on-demand lifecycle check for an external service adapter."""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import tempfile
import time
from typing import Any

try:
    from .ci_history_store import append_record
except ImportError:
    from ci_history_store import append_record

try:
    from .service_adapters import adapter_for, inspect_adapters
    from .execution_scope import ExecutionScope
except ImportError:
    from service_adapters import adapter_for, inspect_adapters
    from execution_scope import ExecutionScope

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(prog="localci service integration")
    parser.add_argument("--kind", choices=sorted(inspect_adapters()), required=True)
    parser.add_argument("--provider", choices=("auto", "host", "docker"), default="auto")
    parser.add_argument("--history-file", type=pathlib.Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    started_at = time.monotonic()
    if args.kind == "postgres":
        # PostgreSQL has a richer initdb/pg_ctl contract and remains covered by
        # its dedicated evaluator; this dispatch keeps the public entrypoint uniform.
        from postgres_integration_check import main as postgres_main
        original_argv = sys.argv
        sys.argv = [original_argv[0], "--provider", args.provider] + (["--json"] if args.json else [])
        if args.history_file:
            sys.argv.extend(["--history-file", str(args.history_file)])
        try:
            return postgres_main()
        finally:
            sys.argv = original_argv
    with tempfile.TemporaryDirectory(prefix=f"localci-{args.kind}-adapter-") as directory, \
            ExecutionScope(run_id=f"service-{args.kind}") as scope:
        root = pathlib.Path(directory)
        adapter = adapter_for(args.kind, root, scope)
        available, reason = adapter.available()
        if not available:
            payload: dict[str, Any] = {"status": "skipped", "kind": args.kind,
                                       "reason": reason,
                                       "duration_seconds": round(time.monotonic() - started_at, 3)}
        else:
            try:
                adapter.start()
                adapter.wait_ready()
                first = adapter.probe()
                adapter.stop()
                disconnected = False
                try:
                    adapter.wait_ready(timeout=1)
                except (OSError, RuntimeError):
                    disconnected = True
                if not disconnected:
                    raise RuntimeError(f"{args.kind} adapter did not detect shutdown")
                recovery_started = time.monotonic()
                adapter.start()
                adapter.wait_ready()
                recovered = adapter.probe()
                payload = {"status": "passed", "kind": args.kind,
                           "port": adapter.port, "adapter": type(adapter).__name__,
                           "lifecycle": [first["status"], "disconnected", recovered["status"]],
                           "recovery_seconds": round(time.monotonic() - recovery_started, 3),
                           "duration_seconds": round(time.monotonic() - started_at, 3)}
            except subprocess.CalledProcessError as error:
                payload = {"status": "skipped" if type(adapter).__name__ == "DockerAdapter" else "failed",
                           "kind": args.kind, "reason": str(error),
                           "error": str(error),
                           "duration_seconds": round(time.monotonic() - started_at, 3)}
            except (OSError, RuntimeError) as error:
                payload = {"status": "failed", "kind": args.kind, "error": str(error),
                           "duration_seconds": round(time.monotonic() - started_at, 3)}
            finally:
                adapter.stop()
    if args.history_file:
        backend = "docker" if payload.get("adapter") == "DockerAdapter" else "host"
        record_payload = {
            "execution_id": f"service-integration-{args.kind}-{int(time.time() * 1000)}",
            "route": {"selected": backend, "requested": args.provider,
                      "status": payload["status"]},
            "result": {
                "status": "success" if payload["status"] == "passed" else payload["status"],
                "backend": backend,
                "results": [{"duration_seconds": payload.get("duration_seconds", 0.0)}],
                "execution_summary": {"total": 1, "completed": 1,
                                       "success": int(payload["status"] == "passed"),
                                       "failed": int(payload["status"] == "failed"),
                                       "not_run": 0},
                "backend_execution": {"provider": backend},
            },
            "service_integration": payload,
        }
        append_record(record_payload, args.history_file.expanduser().resolve(),
                      "service_integration")
    print(json.dumps(payload, ensure_ascii=False) if args.json else
          f"service {args.kind}: {payload['status']}")
    return 0 if payload["status"] in {"passed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
