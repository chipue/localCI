#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from backend_capabilities import detect


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=("host", "docker", "act", "wsl", "windows"), default="host")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = detect(args.backend)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"backend: {result['backend']}")
        print(f"os: {result['os']}")
        print(f"docker: {'available' if result['docker'] else 'unavailable'}")
        print(f"services: {', '.join(result['services']) or 'none'}")
        print(f"tools: {', '.join(name for name, ok in result['tools'].items() if ok) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
