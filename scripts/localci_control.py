"""Manage live localCI runs."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from execution_control import RunControl, control_root


def main() -> int:
    parser = argparse.ArgumentParser(prog="localci control")
    parser.add_argument("action", choices=("cancel", "status"))
    parser.add_argument("run_id")
    parser.add_argument("--control-dir", type=pathlib.Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    control = RunControl(args.run_id, args.control_dir or control_root())
    if args.action == "cancel":
        control.request_cancel()
        payload = {"run_id": args.run_id, "status": "cancellation_requested"}
    else:
        try:
            payload = json.loads(control.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            print(f"unknown localCI run: {args.run_id}", file=sys.stderr)
            return 1
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"{payload.get('run_id', args.run_id)}: {payload.get('status')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
