#!/usr/bin/env python3
"""Deterministic 300-push/day delivery-profile load simulation."""
from __future__ import annotations

import argparse
import heapq
import json
import math
import pathlib
import statistics
from typing import Any


def percentile(values: list[float], value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil((len(ordered) - 1) * value)))
    return round(ordered[index], 3)


def simulate(*, pushes: int, window_hours: float, concurrency: int,
             check_seconds: float, nightly_seconds: float, release_seconds: float,
             runner_cost_per_minute: float, local_detection_rate: float,
             seed: int) -> dict[str, Any]:
    if pushes <= 0 or concurrency <= 0 or window_hours <= 0:
        raise ValueError("pushes, concurrency, and window_hours must be positive")
    window_seconds = window_hours * 3600
    interval = window_seconds / pushes
    slots = [0.0] * concurrency
    waits: list[float] = []
    delays: list[float] = []
    missed_delays: list[float] = []
    for index in range(pushes):
        pushed_at = index * interval
        slot = heapq.heappop(slots)
        started_at = max(pushed_at, slot)
        finished_at = started_at + check_seconds
        heapq.heappush(slots, finished_at)
        waits.append(started_at - pushed_at)
        # Stable pseudo-randomness without a random-state dependency keeps the
        # benchmark reproducible across Python versions.
        detected_locally = ((index * 1103515245 + seed) % 10000) / 10000 < local_detection_rate
        if detected_locally:
            delays.append(finished_at - pushed_at)
        else:
            missed_delays.append(window_seconds - pushed_at + nightly_seconds)
            delays.append(missed_delays[-1])
    nightly_runs = max(1, math.ceil(window_hours / 24))
    release_runs = 1
    runner_minutes = (nightly_runs * nightly_seconds + release_runs * release_seconds) / 60
    return {
        "schema_version": 1,
        "scenario": {"pushes": pushes, "window_hours": window_hours, "seed": seed,
                     "concurrency": concurrency, "local_detection_rate": local_detection_rate},
        "assumptions": {"change_check_seconds": check_seconds,
                        "nightly_seconds": nightly_seconds,
                        "release_gate_seconds": release_seconds,
                        "runner_cost_per_minute": runner_cost_per_minute,
                        "local_execution_cost": 0.0},
        "metrics": {
            "pushes": pushes,
            "wait_seconds_p50": percentile(waits, 0.50),
            "wait_seconds_p95": percentile(waits, 0.95),
            "wait_seconds_max": round(max(waits), 3),
            "detection_delay_seconds_p50": percentile(delays, 0.50),
            "detection_delay_seconds_p95": percentile(delays, 0.95),
            "detection_delay_seconds_max": round(max(delays), 3),
            "nightly_fallbacks": len(missed_delays),
            "nightly_fallback_delay_seconds_p95": percentile(missed_delays, 0.95),
            "runner_minutes": round(runner_minutes, 3),
            "estimated_runner_cost": round(runner_minutes * runner_cost_per_minute, 6),
            "estimated_total_cost": round(runner_minutes * runner_cost_per_minute, 6),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pushes", type=int, default=300)
    parser.add_argument("--window-hours", type=float, default=24.0)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--check-seconds", type=float, default=45.0)
    parser.add_argument("--nightly-seconds", type=float, default=900.0)
    parser.add_argument("--release-seconds", type=float, default=240.0)
    parser.add_argument("--runner-cost-per-minute", type=float, default=0.008)
    parser.add_argument("--local-detection-rate", type=float, default=0.9)
    parser.add_argument("--seed", type=int, default=20261003)
    parser.add_argument("--output", type=pathlib.Path)
    args = parser.parse_args()
    result = simulate(pushes=args.pushes, window_hours=args.window_hours, concurrency=args.concurrency,
                      check_seconds=args.check_seconds, nightly_seconds=args.nightly_seconds,
                      release_seconds=args.release_seconds, runner_cost_per_minute=args.runner_cost_per_minute,
                      local_detection_rate=args.local_detection_rate, seed=args.seed)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
