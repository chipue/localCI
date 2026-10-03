#!/usr/bin/env python3
"""Clear localCI command caches."""
from __future__ import annotations
import argparse
import pathlib
from ci_cache import cache_root, clear

parser = argparse.ArgumentParser(prog="localci cache")
subparsers = parser.add_subparsers(dest="command", required=True)
clear_parser = subparsers.add_parser("clear")
clear_parser.add_argument("--root", type=pathlib.Path, default=pathlib.Path.cwd())
clear_parser.add_argument("--key")
args = parser.parse_args()
if args.command == "clear":
    root = args.root.resolve()
    print(f"localCI cache: removed {clear(root, args.key)} file(s) from {cache_root(root)}")
