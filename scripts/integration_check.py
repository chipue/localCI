#!/usr/bin/env python3
"""Exercise the public plan -> run -> graph path as an integration check."""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="localci-integration-") as directory:
        result_file = pathlib.Path(directory) / "result.json"
        command = ["bash", str(ROOT / "localci"), "run", "--backend", "host",
                   "--profile", "standard", "--root", str(ROOT), "--json",
                   "--result-file", str(result_file), "--history-file", str(pathlib.Path(directory) / "history.jsonl")]
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
        if result.returncode != 0:
            print(result.stdout, end="")
            print(result.stderr, end="", file=sys.stderr)
            return result.returncode
        payload = json.loads(result.stdout)
        if payload.get("result", {}).get("status") != "success":
            print("integration check failed: standard route did not succeed")
            return 1
        graph = subprocess.run(["bash", str(ROOT / "localci"), "graph", str(result_file), "--json"],
                               cwd=ROOT, text=True, capture_output=True)
        if graph.returncode != 0 or not json.loads(graph.stdout).get("nodes"):
            print("integration check failed: graph did not contain nodes")
            return 1
    print("integration check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
