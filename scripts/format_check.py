#!/usr/bin/env python3
"""Check common text-format invariants without requiring a formatter package."""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXTENSIONS = {".py", ".sh", ".ps1", ".md", ".json", ".yml", ".yaml"}
SKIP_PARTS = {".git", "__pycache__", "dist", ".localci-logs"}


def files() -> list[pathlib.Path]:
    return sorted(path for path in ROOT.rglob("*")
                  if path.is_file() and path.suffix in EXTENSIONS
                  and not SKIP_PARTS.intersection(path.parts))


def main() -> int:
    errors: list[str] = []
    checked = files()
    for path in checked:
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(ROOT)
        for line_number, line in enumerate(text.splitlines(), start=1):
            if line.rstrip(" \t") != line:
                errors.append(f"{relative}:{line_number}: trailing whitespace")
        if text and not text.endswith("\n"):
            errors.append(f"{relative}: missing final newline")
    if errors:
        print("format check failed")
        print("\n".join(errors))
        return 1
    print(f"format check passed ({len(checked)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
