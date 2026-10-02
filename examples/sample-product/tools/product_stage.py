#!/usr/bin/env python3
"""Dependency-free product stages used by the localCI evaluation fixture."""

from __future__ import annotations

import compileall
import os
import pathlib
import shutil
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
INSTALLED = ROOT / ".installed"
DIST = ROOT / "dist"


def install() -> None:
    INSTALLED.mkdir(parents=True, exist_ok=True)
    target = INSTALLED / "sample_product"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(SOURCE / "sample_product", target)


def test() -> None:
    environment = {"PYTHONPATH": str(SOURCE)}
    subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "examples/sample-product/tests", "-p", "test_*.py"],
        check=True,
        env={**os.environ, **environment},
    )


def typecheck() -> None:
    if not compileall.compile_dir(str(SOURCE), quiet=1):
        raise SystemExit("sample-product typecheck failed")


def build() -> None:
    DIST.mkdir(parents=True, exist_ok=True)
    output = DIST / "sample_product.pyz"
    subprocess.run(
        [sys.executable, "-m", "zipapp", str(SOURCE), "-o", str(output)],
        check=True,
    )


STAGES = {"install": install, "test": test, "typecheck": typecheck, "build": build}


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in STAGES:
        raise SystemExit(f"usage: {pathlib.Path(sys.argv[0]).name} <{'|'.join(STAGES)}>")
    STAGES[sys.argv[1]]()
