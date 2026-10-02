#!/usr/bin/env python3
"""Perform a small, conservative secret scan using only the standard library."""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXTENSIONS = {".py", ".sh", ".ps1", ".md", ".json", ".yml", ".yaml"}
SKIP_PARTS = {".git", "__pycache__", "dist", ".localci-logs"}
PATTERNS = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key"),
    (re.compile(r"(?i)gh[pousr]_[A-Za-z0-9_]{20,}"), "GitHub token"),
)


def main() -> int:
    findings: list[str] = []
    checked = 0
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in EXTENSIONS or SKIP_PARTS.intersection(path.parts):
            continue
        checked += 1
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for pattern, label in PATTERNS:
                if pattern.search(line):
                    findings.append(f"{path.relative_to(ROOT)}:{line_number}: {label}")
    if findings:
        print("security scan failed")
        print("\n".join(findings))
        return 1
    print(f"security scan passed ({checked} files scanned)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
