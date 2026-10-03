"""Secret handling and child-process environment policy for localCI."""
from __future__ import annotations

import os
import re
from typing import Any, Iterable

SAFE_ENV_NAMES = {
    "CI", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "LOGNAME", "PATH",
    "PWD", "SHELL", "SHLVL", "SYSTEMROOT", "TEMP", "TERM", "TMP",
    "TMPDIR", "USER", "USERNAME", "WINDIR",
}
SECRET_NAME_RE = re.compile(r"(?i)(?:secret|token|password|passwd|api[_-]?key|credential|private[_-]?key)")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
INTERNAL_ENV_NAMES = {
    "LOCALCI_BACKEND", "LOCALCI_EXECUTOR_RUN", "LOCALCI_EXECUTION_ID",
    "LOCALCI_PORT_NAMESPACE", "LOCALCI_RUN_ID", "LOCALCI_RUNTIME_DIR",
    "LOCALCI_TEMP_DIR", "AGENT_CI_BACKEND", "AGENT_CI_MANIFEST",
    "AGENT_CI_PROFILE", "AGENT_CI_BASE", "AGENT_CI_LOG_DIR",
}


def _as_names(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value if isinstance(item, str) and ENV_NAME_RE.fullmatch(item)}


def policy_for(item: dict[str, Any], plan_security: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge plan and command security settings without exposing secret values."""
    policy = dict(plan_security or {})
    command_policy = item.get("security", {})
    if isinstance(command_policy, dict):
        policy.update(command_policy)
    allowlist = _as_names(policy.get("env_allowlist"))
    allowlist.update(SAFE_ENV_NAMES)
    allowlist.update(INTERNAL_ENV_NAMES)
    requirements = item.get("requirements", {})
    required = _as_names(requirements.get("env") if isinstance(requirements, dict) else None)
    allowlist.update(required)
    secret_names = _as_names(policy.get("secret_env")) | required
    allowlist.update(secret_names)
    secret_names.update(name for name in allowlist if SECRET_NAME_RE.search(name))
    log_policy = policy.get("secret_log_policy", "redact")
    if log_policy not in {"redact", "discard"}:
        raise ValueError("security.secret_log_policy must be redact or discard")
    return {"env_allowlist": sorted(allowlist), "secret_env": sorted(secret_names),
            "secret_log_policy": log_policy}


def child_environment(policy: dict[str, Any], base: dict[str, str] | None = None) -> dict[str, str]:
    source = base if base is not None else os.environ
    allowlist = set(policy.get("env_allowlist", []))
    return {name: str(value) for name, value in source.items() if name in allowlist}


def secret_values(policy: dict[str, Any], environment: dict[str, str] | None = None) -> list[str]:
    source = environment if environment is not None else os.environ
    values = {source[name] for name in policy.get("secret_env", [])
              if name in source and len(source[name]) >= 4}
    return sorted(values, key=len, reverse=True)


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    """Redact exact secret values and common credential formats from text."""
    for value in sorted({value for value in secrets if value}, key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
    patterns = (
        re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer|basic)\s+)[^\s,;]+"),
        re.compile(r"(?i)((?:token|secret|password|passwd|api[_-]?key)\s*[=:]\s*)[^\s,;\"']+"),
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    )
    for pattern in patterns:
        text = pattern.sub(lambda match: (match.group(1) if match.lastindex else "") + "[REDACTED]", text)
    return "".join(char if char in "\t\n\r" or ord(char) >= 32 else "?" for char in text)
