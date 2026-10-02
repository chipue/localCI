#!/usr/bin/env python3
"""Install a preflight template and register it in a product manifest."""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]

TEMPLATES: dict[str, dict[str, Any]] = {
    "node": {
        "source": "node-server-preflight.example.js",
        "config_source": "node-server-preflight.example.json",
        "script": "node-server-preflight.js",
        "config": "node-server-preflight.json",
        "runner": "node",
        "tools": ["node"],
        "timeout": 300,
    },
    "python": {
        "source": "python-service-preflight.example.py",
        "config_source": "python-service-preflight.example.json",
        "script": "python-service-preflight.py",
        "config": "python-service-preflight.json",
        "runner": "python3",
        "tools": ["python3"],
        "timeout": 300,
    },
    "http": {
        "source": "http-service-preflight.example.py",
        "config_source": "http-service-preflight.example.json",
        "script": "http-service-preflight.py",
        "config": "http-service-preflight.json",
        "runner": "python3",
        "tools": ["python3"],
        "timeout": 300,
    },
    "postgres": {
        "source": "postgres-preflight.example.py",
        "config_source": "postgres-preflight.example.json",
        "script": "postgres-preflight.py",
        "config": "postgres-preflight.json",
        "runner": "python3",
        "tools": ["python3", "pg_isready", "psql"],
        "timeout": 300,
    },
}


def command_for(spec: dict[str, Any]) -> str:
    return f"{spec['runner']} .localci/preflight/{spec['script']} --config .localci/preflight/{spec['config']}"


def load_manifest(path: pathlib.Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("commands"), list):
        raise ValueError(f"{path} must contain a commands list")
    return data


def install(root: pathlib.Path, kind: str, manifest: pathlib.Path,
            attach_command: str | None, force: bool) -> dict[str, Any]:
    spec = TEMPLATES[kind]
    source_dir = ROOT / ".localci" / "preflight"
    target_dir = root / ".localci" / "preflight"
    target_dir.mkdir(parents=True, exist_ok=True)
    installed = []
    for source_name, target_name in ((spec["source"], spec["script"]),
                                     (spec["config_source"], spec["config"])):
        source = source_dir / source_name
        target = target_dir / target_name
        if target.exists() and not force:
            raise FileExistsError(f"already exists: {target} (use --force to replace)")
        shutil.copy2(source, target)
        installed.append(str(target))

    data = load_manifest(manifest)
    commands = data["commands"]
    command_text = command_for(spec)
    registration = "standalone"
    if attach_command:
        item = next((item for item in commands if item.get("name") == attach_command), None)
        if item is None:
            raise ValueError(f"manifest command not found: {attach_command}")
        preflight = item.setdefault("preflight", [])
        if isinstance(preflight, str):
            preflight = [preflight]
            item["preflight"] = preflight
        if command_text not in preflight:
            preflight.append(command_text)
        registration = f"attached:{attach_command}"
    else:
        name = f"preflight_{kind}"
        existing = next((item for item in commands if item.get("name") == name), None)
        if existing is not None and not force:
            raise ValueError(f"manifest command already exists: {name} (use --force to replace)")
        generated = {
            "name": name,
            "stage": "custom",
            "command": command_text,
            "requirements": {
                "os": ["linux", "macos", "windows"],
                "docker": False,
                "services": ["postgres"],
                "tools": spec["tools"],
                "packages": [],
                "env": [],
            },
            "always": True,
            "profiles": ["standard", "full"],
            "timeout_seconds": spec["timeout"],
            "retryable": True,
        }
        if existing is not None:
            commands[commands.index(existing)] = generated
        else:
            commands.append(generated)
        registration = f"standalone:{name}"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"kind": kind, "files": installed, "manifest": str(manifest),
            "registration": registration, "command": command_text}


def main() -> int:
    parser = argparse.ArgumentParser(prog="localci preflight init")
    parser.add_argument("--kind", required=True, choices=sorted(TEMPLATES))
    parser.add_argument("--root", type=pathlib.Path, default=ROOT)
    parser.add_argument("--manifest", type=pathlib.Path)
    parser.add_argument("--command", help="Attach the preflight to an existing manifest command")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    manifest = args.manifest or (root / ".localci" / "product-commands.json")
    if not manifest.is_absolute():
        manifest = root / manifest
    try:
        result = install(root, args.kind, manifest, args.command, args.force)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"preflight init failed: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"preflight initialized: {result['kind']}")
        for path in result["files"]:
            print(f"  - file: {path}")
        print(f"  - registration: {result['registration']}")
        print(f"  - command: {result['command']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
