#!/usr/bin/env python3
"""Run the product tests under stdlib tracing as a dependency-free coverage smoke check."""
from __future__ import annotations

import pathlib
import sys
import unittest
from trace import Trace

ROOT = pathlib.Path(__file__).resolve().parents[1]


def run_tests() -> bool:
    # Executing this script sets sys.path[0] to scripts/, so explicitly add
    # the repository root before discovery. Tests import the scripts package
    # directly and must behave the same under coverage as under unittest.
    root_string = str(ROOT)
    if root_string not in sys.path:
        sys.path.insert(0, root_string)
    suite = unittest.defaultTestLoader.discover(str(ROOT / "eval"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=0).run(suite)
    return result.wasSuccessful()


def exercise_core_router() -> None:
    """Trace the in-process Router and graph modules too.

    The test suite intentionally invokes public CLIs in subprocesses, which the
    stdlib tracer cannot follow. This small pass keeps this dependency-free
    check useful without pretending to be a replacement for coverage.py.
    """
    sys.path.insert(0, str(ROOT))
    from scripts.localci_graph import make_graph
    from scripts.localci_plan import make_plan

    plan = make_plan(ROOT, ROOT / ".localci/product-commands.json", "standard", "host", None)
    make_graph({
        "plan": plan,
        "result": {"results": [], "execution_summary": {"rerun_candidates": []}},
    })


def main() -> int:
    tracer = Trace(count=True, trace=False)
    passed = tracer.runfunc(run_tests)
    tracer.runfunc(exercise_core_router)
    counts = tracer.results().counts
    if not passed:
        return 1
    if not counts:
        print("coverage check failed: no traced lines")
        return 1
    touched = len({filename for filename, _line in counts
                   if str(ROOT / "scripts") in filename})
    print(f"coverage smoke check passed ({touched} script files traced)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
