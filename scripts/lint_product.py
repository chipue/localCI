#!/usr/bin/env python3
"""Run dependency-free lint checks for the localCI Python sources."""
from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCES = [ROOT / "scripts", ROOT / "eval"]


def main() -> int:
    errors: list[str] = []
    files = [path for directory in SOURCES for path in directory.rglob("*.py")
             if "__pycache__" not in path.parts]
    for path in sorted(files):
        text = path.read_text(encoding="utf-8")
        try:
            ast.parse(text, filename=str(path))
        except SyntaxError as error:
            errors.append(f"{path.relative_to(ROOT)}:{error.lineno}: {error.msg}")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if line.startswith("\t"):
                errors.append(f"{path.relative_to(ROOT)}:{line_number}: tab indentation")
    if errors:
        print("lint failed")
        print("\n".join(errors))
        return 1
    print(f"lint passed ({len(files)} Python files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
