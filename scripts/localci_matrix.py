"""Compare product CI plan availability across supported local backends."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from localci_plan import make_plan
from backend_adapters import BACKENDS

ROOT = pathlib.Path(__file__).resolve().parents[1]


def make_matrix(root: pathlib.Path, inventory: pathlib.Path, profile: str, base: str | None) -> dict:
    results = {}
    for backend in BACKENDS:
        plan = make_plan(root, inventory, profile, backend, base)
        results[backend] = {
            "runtime_available": plan["capabilities"].get("runtime_available", True),
            "os": plan["target_os"],
            "docker": plan["capabilities"]["docker"],
            "tools": plan["capabilities"].get("tools", {}),
            "shell": plan["capabilities"].get("shell"),
            "act_architecture": plan["capabilities"].get("act_architecture"),
            "act_image": plan["capabilities"].get("act_image"),
            "act_image_available": plan["capabilities"].get("act_image_available"),
            "docker_image": plan["capabilities"].get("docker_image"),
            "wsl_command": plan["capabilities"].get("wsl_command"),
            "selected": [item["name"] for item in plan["selected"]],
            "blocked": plan["blocked"],
            "excluded": plan["excluded"],
            "execution_allowed": plan["execution_allowed"],
        }
    return {"profile": profile, "backends": results}


def main() -> int:
    parser = argparse.ArgumentParser(prog="localci matrix")
    parser.add_argument("--root", type=pathlib.Path, default=ROOT)
    parser.add_argument("--base")
    parser.add_argument("--profile", choices=("quick", "standard", "full"), default="standard")
    parser.add_argument("--inventory", type=pathlib.Path, default=pathlib.Path(".localci/product-commands.json"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    inventory = args.inventory if args.inventory.is_absolute() else root / args.inventory
    try:
        matrix = make_matrix(root, inventory, args.profile, args.base)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"matrix error: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(matrix, ensure_ascii=False, indent=2))
        return 0
    print(f"localCI matrix (profile: {matrix['profile']})")
    for backend, result in matrix["backends"].items():
        state = "runnable" if result["execution_allowed"] else "blocked"
        runtime = "available" if result["runtime_available"] else "unavailable"
        print(f"{backend}: {state}, runtime={runtime}, os={result['os']}")
        if result["blocked"]:
            for item in result["blocked"]:
                print(f"  - {item['name']}: missing {', '.join(item['missing'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
