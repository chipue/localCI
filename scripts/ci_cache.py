"""Content-addressed, local caches for CI command inputs and outputs."""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import tarfile
import tempfile
from typing import Any


def cache_root(root: pathlib.Path) -> pathlib.Path:
    configured = os.environ.get("LOCALCI_CACHE_DIR")
    return pathlib.Path(configured).expanduser() if configured else pathlib.Path.home() / ".localci" / "cache"


def _files_for_patterns(root: pathlib.Path, patterns: list[str]) -> list[pathlib.Path]:
    found: set[pathlib.Path] = set()
    for pattern in patterns:
        for path in root.glob(pattern):
            if ".localci" in path.relative_to(root).parts:
                continue
            if path.is_file():
                found.add(path)
            elif path.is_dir():
                found.update(item for item in path.rglob("*")
                             if item.is_file() and ".localci" not in item.relative_to(root).parts)
    return sorted(found, key=lambda item: item.relative_to(root).as_posix())


def _config(item: dict[str, Any]) -> dict[str, Any] | None:
    value = item.get("cache")
    if value is False or value is None:
        return None
    if value is True:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("cache must be an object or false")
    paths = value.get("paths", [])
    key_files = value.get("key_files", [])
    if not isinstance(paths, list) or not all(isinstance(v, str) and v for v in paths):
        raise ValueError("cache.paths must be a list of strings")
    if not isinstance(key_files, list) or not all(isinstance(v, str) and v for v in key_files):
        raise ValueError("cache.key_files must be a list of strings")
    if not paths:
        return None
    return {"version": str(value.get("version", "1")), "paths": paths, "key_files": key_files,
            "key": str(value.get("key", ""))}


def cache_key(item: dict[str, Any], root: pathlib.Path, backend: str) -> tuple[str, dict[str, Any]] | None:
    config = _config(item)
    if config is None:
        return None
    digest = hashlib.sha256()
    digest.update(json.dumps({"version": config["version"], "key": config["key"],
                              "command": item.get("command", ""), "backend": backend,
                              "platform": os.name}, sort_keys=True).encode())
    inputs: list[dict[str, Any]] = []
    for path in _files_for_patterns(root, config["key_files"]):
        relative = path.relative_to(root).as_posix()
        content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        digest.update(relative.encode() + b"\0" + content_hash.encode())
        inputs.append({"path": relative, "sha256": content_hash})
    return digest.hexdigest(), {**config, "inputs": inputs}


def _archive_path(root: pathlib.Path, key: str) -> pathlib.Path:
    return cache_root(root) / f"{key}.tar.gz"


def _safe_member(name: str) -> pathlib.PurePosixPath:
    path = pathlib.PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"unsafe cache archive member: {name}")
    return path


def restore(item: dict[str, Any], root: pathlib.Path, backend: str, invalidate: bool = False) -> dict[str, Any]:
    keyed = cache_key(item, root, backend)
    if keyed is None:
        return {"status": "disabled"}
    key, metadata = keyed
    archive = _archive_path(root, key)
    metadata_path = archive.with_suffix(".json")
    if invalidate:
        archive.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
    if not archive.is_file() or not metadata_path.is_file():
        return {"status": "miss", "key": key}
    try:
        saved = json.loads(metadata_path.read_text(encoding="utf-8"))
        if hashlib.sha256(archive.read_bytes()).hexdigest() != saved["archive_sha256"]:
            raise ValueError("cache archive checksum mismatch")
        with tarfile.open(archive, "r:gz") as handle:
            members = handle.getmembers()
            for member in members:
                _safe_member(member.name)
                if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                    raise ValueError("unsupported cache archive member")
            with tempfile.TemporaryDirectory(prefix="localci-cache-") as temporary:
                target = pathlib.Path(temporary)
                handle.extractall(target)
                for member in members:
                    relative = _safe_member(member.name)
                    source = target.joinpath(*relative.parts)
                    destination = root.joinpath(*relative.parts)
                    if member.isdir():
                        destination.mkdir(parents=True, exist_ok=True)
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, destination)
        return {"status": "hit", "key": key, "paths": metadata["paths"]}
    except (OSError, ValueError, KeyError, json.JSONDecodeError, tarfile.TarError):
        archive.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
        return {"status": "corrupt", "key": key, "action": "invalidated_and_rerun"}


def save(item: dict[str, Any], root: pathlib.Path, backend: str) -> dict[str, Any]:
    keyed = cache_key(item, root, backend)
    if keyed is None:
        return {"status": "disabled"}
    key, metadata = keyed
    paths = _files_for_patterns(root, metadata["paths"])
    if not paths:
        return {"status": "not_saved", "key": key, "reason": "no cache paths exist"}
    destination = _archive_path(root, key)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(f".{os.getpid()}.tmp")
    try:
        with tarfile.open(temporary, "w:gz") as handle:
            for path in paths:
                handle.add(path, arcname=path.relative_to(root).as_posix(), recursive=path.is_dir())
        archive_hash = hashlib.sha256(temporary.read_bytes()).hexdigest()
        os.replace(temporary, destination)
        destination.with_suffix(".json").write_text(
            json.dumps({**metadata, "archive_sha256": archive_hash}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        return {"status": "saved", "key": key, "paths": metadata["paths"]}
    finally:
        temporary.unlink(missing_ok=True)


def clear(root: pathlib.Path, key: str | None = None) -> int:
    directory = cache_root(root)
    candidates = ([directory / f"{key}.tar.gz", directory / f"{key}.json"] if key else
                  list(directory.glob("*.tar.gz")) + list(directory.glob("*.json")))
    removed = 0
    for path in candidates:
        if path.is_file():
            path.unlink()
            removed += 1
    return removed
