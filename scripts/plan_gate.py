"""Safety gate between the CI Router and a Local Executor."""
from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any


class PlanBlockedError(RuntimeError):
    """Raised when a plan contains commands that cannot run on its backend."""


def ensure_runnable(plan: dict[str, Any]) -> None:
    """Reject blocked plans before an executor is given any selected command."""
    blocked = plan.get("blocked", [])
    if blocked or plan.get("execution_allowed") is not True:
        details = []
        for item in blocked:
            details.append(f"{item.get('name', 'unnamed')}: {', '.join(item.get('missing', []))}")
        reason = "; ".join(details) or str(plan.get("execution_blocked_reason", "plan is blocked"))
        raise PlanBlockedError(f"Local Executor handoff refused: {reason}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="plan_gate")
    parser.add_argument("plan", type=pathlib.Path, help="JSON plan produced by localci plan --json")
    args = parser.parse_args()
    try:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        ensure_runnable(plan)
    except (OSError, json.JSONDecodeError, PlanBlockedError) as error:
        print(f"plan gate: {error}")
        return 2
    print("plan gate: runnable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
