#!/usr/bin/env python3
"""Print a short, redacted excerpt from a failed local-CI log."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys


ERROR_RE = re.compile(
    r"(?:^|\W)(?:fatal|error|failed|failure|traceback|exception|npm ERR!)(?:\W|$)",
    re.IGNORECASE,
)
SECRET_RES = (
    re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer|basic)\s+)[^\s,;]+"),
    re.compile(r"(?i)((?:token|secret|password|passwd|api[_-]?key)\s*[=:]\s*)[^\s,;\"']+"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
)
MAX_LINE_CHARS = 500


def redact(line: str) -> str:
    line = "".join(char if char in "\t\n" or ord(char) >= 32 else "?" for char in line)
    for pattern in SECRET_RES:
        line = pattern.sub("[REDACTED]", line)
    line = line.rstrip("\r\n")
    if len(line) > MAX_LINE_CHARS:
        return line[:MAX_LINE_CHARS] + "...[truncated]"
    return line


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path)
    parser.add_argument("--max-lines", type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.max_lines <= 80:
        parser.error("--max-linesは1から80の範囲にしてください")

    try:
        lines = args.log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as error:
        print(f"CIログを読み込めません: {error}", file=sys.stderr)
        return 2

    matches = [index for index, line in enumerate(lines) if ERROR_RE.search(line)]
    if matches:
        focus = matches[-1]
        start = max(0, focus - min(8, args.max_lines // 2))
        selected = lines[start:start + args.max_lines]
    else:
        selected = lines[-args.max_lines:]

    print("error_context:", file=sys.stderr)
    for line in selected:
        print(redact(line), file=sys.stderr)
    if len(lines) > len(selected):
        print("...[output truncated; inspect the protected full log locally]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
