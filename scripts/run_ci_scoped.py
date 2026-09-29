#!/usr/bin/env python3
"""Bootstrap scoped runner; product-specific scope rules are added later."""
from __future__ import annotations

import pathlib
import subprocess
import sys

root = pathlib.Path(__file__).resolve().parents[1]
completed = subprocess.run([sys.executable, "scripts/policy_verify.py"], cwd=root)
if completed.returncode == 0:
    print("scoped checks: not configured; policy checks passed")
sys.exit(completed.returncode)
