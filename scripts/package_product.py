#!/usr/bin/env python3
"""Create a cross-platform source package for localCI."""
from __future__ import annotations

import pathlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dist" / "localci.zip"
REQUIRED = ("localci", "packaging.ps1", "README.md", "WORKFLOW.md", "scripts", ".localci")
OPTIONAL = ("docs", ".github")


def add_path(archive: zipfile.ZipFile, path: pathlib.Path) -> None:
    if path.is_dir():
        for child in sorted(path.rglob("*")):
            if child.is_file() and "__pycache__" not in child.parts and child.suffix != ".pyc":
                archive.write(child, pathlib.Path("localci") / child.relative_to(ROOT))
    elif path.is_file():
        archive.write(path, pathlib.Path("localci") / path.relative_to(ROOT))


def main() -> int:
    missing = [name for name in REQUIRED if not (ROOT / name).exists()]
    if missing:
        print("package inputs missing: " + ", ".join(missing))
        return 1
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    if OUTPUT.exists():
        OUTPUT.unlink()
    with zipfile.ZipFile(OUTPUT, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in REQUIRED + OPTIONAL:
            path = ROOT / name
            if path.exists():
                add_path(archive, path)
    print(f"created {OUTPUT} ({OUTPUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
