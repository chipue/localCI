#!/usr/bin/env python3
"""Check that the public localci CLI and its core subcommands start successfully."""
from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> int:
    for arguments in (("plan", "--help"), ("run", "--help"),
                      ("retry", "--help"), ("graph", "--help")):
        result = subprocess.run(["bash", str(ROOT / "localci"), *arguments],
                                cwd=ROOT, text=True, capture_output=True)
        if result.returncode != 0:
            print(result.stderr, end="", file=sys.stderr)
            return result.returncode
    print("smoke check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
