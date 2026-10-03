#!/usr/bin/env python3
"""Run a non-CI command with bounded output and a protected full log."""

from __future__ import annotations

import argparse
from collections import deque
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="非CIコマンドの全文をローカルログへ保存し、要約だけ表示します。"
    )
    parser.add_argument("--label", default="command")
    parser.add_argument("--success-output", choices=("none", "head", "tail"), default="none")
    parser.add_argument("--max-lines", type=int, default=20)
    parser.add_argument("--log-dir")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if not args.command:
        parser.error("-- の後に実行するコマンドが必要です")
    if not 1 <= args.max_lines <= 80:
        parser.error("--max-linesは1から80の範囲にしてください")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,40}", args.label):
        parser.error("--labelは英数字、点、下線、ハイフンの40文字以内にしてください")
    return args


def redact(value: str) -> str:
    value = "".join(char if char in "\t\n" or ord(char) >= 32 else "?" for char in value)
    for pattern in SECRET_RES:
        value = pattern.sub(lambda match: match.group(0)[:match.group(0).find("=") + 1] + "[REDACTED]" if "=" in match.group(0) else "[REDACTED]", value)
    return value


def bounded(value: str) -> str:
    value = redact(value.rstrip("\r\n"))
    return value if len(value) <= MAX_LINE_CHARS else value[:MAX_LINE_CHARS] + "...[truncated]"


def log_dir(raw: str | None) -> Path:
    default = Path(os.environ.get("TMPDIR", "/tmp")) / "agent-command-logs"
    path = Path(raw or os.environ.get("AGENT_COMMAND_LOG_DIR", str(default)))
    if not path.is_absolute():
        raise ValueError("ログ保存先は絶対パスで指定してください")
    if path.exists() and (path.is_symlink() or not path.is_dir()):
        raise ValueError("ログ保存先は通常のディレクトリで指定してください")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_mode & 0o077:
        raise ValueError("ログ保存先は所有者だけが読める権限にしてください")
    return path


def main() -> int:
    args = parse_args()
    try:
        destination = log_dir(args.log_dir)
    except (OSError, ValueError) as error:
        print(f"静かなコマンド実行を開始できません: {error}", file=sys.stderr)
        return 2

    descriptor, raw_path = tempfile.mkstemp(prefix="command-", dir=destination)
    path = Path(raw_path)
    os.fchmod(descriptor, 0o600)
    started = time.monotonic()
    try:
        with os.fdopen(descriptor, "wb") as output:
            try:
                status = subprocess.run(args.command, stdout=output, stderr=subprocess.STDOUT).returncode
            except FileNotFoundError:
                output.write(b"command executable was not found\n")
                status = 127
            except PermissionError:
                output.write(b"command executable was not permitted\n")
                status = 126
    except KeyboardInterrupt:
        status = 130

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    tail = deque((bounded(line) for line in lines), maxlen=args.max_lines)
    error_index = next((index for index, line in enumerate(lines) if ERROR_RE.search(line)), None)
    selected = list(tail)
    if error_index is not None:
        selected = [bounded(line) for line in lines[max(0, error_index - 5):error_index + args.max_lines]]
    elapsed = time.monotonic() - started

    if status == 0:
        print("非CIコマンド成功")
        print(f"label: {args.label}")
        print(f"exit_code: {status}")
        print(f"duration_seconds: {elapsed:.1f}")
        print(f"output: lines={len(lines)} bytes={path.stat().st_size}")
        print(f"full_log: {path}")
        if args.success_output != "none":
            excerpt = lines[:args.max_lines] if args.success_output == "head" else lines[-args.max_lines:]
            print("output_excerpt:")
            print("\n".join(bounded(line) for line in excerpt))
        return 0

    print("非CIコマンド失敗", file=sys.stderr)
    print(f"label: {args.label}", file=sys.stderr)
    print(f"exit_code: {status}", file=sys.stderr)
    print(f"duration_seconds: {elapsed:.1f}", file=sys.stderr)
    print(f"full_log: {path}", file=sys.stderr)
    print("error_context:", file=sys.stderr)
    print("\n".join(selected), file=sys.stderr)
    return status if 0 <= status <= 255 else 1


if __name__ == "__main__":
    raise SystemExit(main())
