"""Registry separating the required localCI core from optional backends."""
from __future__ import annotations

from typing import Any

from backend_capabilities import detect

CORE_BACKENDS = ("host",)
OPTIONAL_BACKENDS = ("docker", "act", "wsl", "windows")
BACKENDS = CORE_BACKENDS + OPTIONAL_BACKENDS

ADAPTERS: dict[str, dict[str, Any]] = {
    "host": {"kind": "core", "description": "Run commands on the current OS"},
    "docker": {"kind": "optional", "description": "Run commands in a Docker container"},
    "act": {"kind": "optional", "description": "Run GitHub Actions workflows locally"},
    "wsl": {"kind": "optional", "description": "Run Linux commands through WSL"},
    "windows": {"kind": "optional", "description": "Run Windows PowerShell commands"},
}


def inspect_backends() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for backend in BACKENDS:
        capabilities = detect(backend)
        result[backend] = {
            **ADAPTERS[backend],
            "backend": backend,
            "available": bool(capabilities.get("runtime_available")),
            "capabilities": capabilities,
        }
    return result
