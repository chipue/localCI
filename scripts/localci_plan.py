#!/usr/bin/env python3
"""Create a localCI plan from product commands and Git changes."""
from __future__ import annotations
import argparse
import fnmatch
import json
import pathlib
import shutil
import subprocess
import sys
from typing import Any

from backend_capabilities import detect, missing_requirements
from backend_adapters import BACKENDS
from validate_command_manifest import command_executables, validate

ROOT = pathlib.Path(__file__).resolve().parents[1]

# Changes to these files can alter the meaning of every product test.  A
# differential plan must therefore stop trying to infer a narrow test set.
DIFF_CONFIG_PATTERNS = (
    ".localci/**", ".github/**", ".agent-ci-policy.yml", "AGENTS.md",
    "WORKFLOW.md", "Dockerfile", "Dockerfile.*", "docker-compose*",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "requirements*.txt", "pyproject.toml", "poetry.lock", "Pipfile.lock",
)

def discover(root: pathlib.Path, base: str | None) -> dict[str, Any]:
    # Discovery belongs to localCI, not to the product repository.  This keeps
    # the Router usable with a product that only provides a command manifest.
    discovery_script = ROOT / "scripts/discover_changes.py"
    command = [sys.executable, str(discovery_script), "--root", str(root), "--json"]
    if base:
        command.extend(["--base", base])
    try:
        subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--git-dir"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError:
        return {
            "root": str(root),
            "base": base,
            "count": 0,
            "changed_files": [],
            "source": "non-git-root",
        }
    try:
        result = subprocess.run(command, cwd=root, check=True, text=True, capture_output=True)
    except subprocess.CalledProcessError as error:
        # A manifest can be evaluated before a product repository has been
        # initialized as Git (for example in a package/build sandbox).  In
        # that case path-filtered commands cannot be inferred, while commands
        # marked `always` remain valid and should still be routable.
        error_output = f"{error.stdout or ''}\n{error.stderr or ''}".lower()
        if "not a git repository" in error_output:
            return {
                "root": str(root),
                "base": base,
                "count": 0,
                "changed_files": [],
                "source": "non-git-root",
            }
        raise
    return json.loads(result.stdout)

def load_inventory(path: pathlib.Path) -> list[dict[str, Any]]:
    errors = validate(path)
    if errors:
        raise ValueError("invalid command manifest: " + "; ".join(errors))
    data = json.loads(path.read_text(encoding="utf-8"))
    commands = data.get("commands")
    if not isinstance(commands, list):
        raise ValueError("manifest commands must be a list")
    return commands


def load_security(path: pathlib.Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    security = data.get("security", {})
    if security is None:
        return {}
    if not isinstance(security, dict):
        raise ValueError("manifest security must be an object")
    return security


def missing_command_executables(item: dict[str, Any], root: pathlib.Path) -> list[str]:
    """Find executable names in the selected shell command that are absent."""
    missing: list[str] = []
    shell_builtins = {"case", "do", "done", "echo", "else", "esac", "exit", "export",
                      "fi", "for", "if", "read", "return", "set", "shift", "source",
                      "test", "then", "true", "type", "unset", "until", "while"}
    for executable in command_executables(str(item.get("command", ""))):
        if executable in shell_builtins or executable.startswith("${"):
            continue
        if "/" in executable:
            candidate = pathlib.Path(executable)
            if not candidate.is_absolute():
                candidate = root / candidate
            available = candidate.is_file() and (candidate.stat().st_mode & 0o111 != 0)
        else:
            available = shutil.which(executable) is not None
        if not available and executable not in missing:
            missing.append(executable)
    return missing

def path_matches(item: dict[str, Any], changed: set[str], respect_always: bool = True) -> bool:
    if respect_always and item.get("always", False):
        return True
    patterns = item.get("paths", [])
    if not patterns:
        return True
    return any(fnmatch.fnmatch(path, pattern) for path in changed for pattern in patterns)


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _dependency_closure(selected: list[dict[str, Any]], commands: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Add declared setup dependencies and report references we cannot trust."""
    by_name = {str(item.get("name", "unnamed")): item for item in commands}
    names = {str(item.get("name", "unnamed")) for item in selected}
    unknown: list[str] = []
    pending = list(selected)
    while pending:
        item = pending.pop()
        for dependency in item.get("depends_on", []):
            dependency = str(dependency)
            if dependency not in by_name:
                unknown.append(f"{item.get('name', 'unnamed')} depends_on unknown command {dependency}")
            elif dependency not in names:
                names.add(dependency)
                pending.append(by_name[dependency])
    return [item for item in commands if str(item.get("name", "unnamed")) in names], unknown


def _diff_selection(commands: list[dict[str, Any]], changed_files: list[dict[str, Any]], profile: str,
                    source: str) -> tuple[list[dict[str, Any]], str, list[str]]:
    """Return a conservative differential selection and its safety decision."""
    changed = {str(item["path"]) for item in changed_files}
    reasons: list[str] = []
    if source in {"non-git-root", "git-unavailable"}:
        reasons.append("change discovery unavailable: " + source)
    if not changed:
        reasons.append("no changed files were discovered")
    config_changes = sorted(path for path in changed if _matches_any(path, DIFF_CONFIG_PATTERNS))
    if config_changes:
        reasons.append("shared configuration changed: " + ", ".join(config_changes[:5]))

    eligible = [item for item in commands if profile in set(item.get("profiles", ["standard"]))]
    full_eligible = [item for item in commands if "full" in set(item.get("profiles", ["standard"]))]
    # A test-like command without paths has an unknown impact surface.  Setup
    # commands are safe only when pulled in through an explicit dependency.
    unknown_scope = [str(item.get("name", "unnamed")) for item in eligible
                     if not item.get("paths") and str(item.get("stage")) in
                     {"test", "typecheck", "integration_test", "full_ci", "custom"}]
    if unknown_scope:
        reasons.append("test scope is undeclared for: " + ", ".join(unknown_scope))
    if reasons:
        return full_eligible, "full", reasons

    targets = [item for item in eligible if path_matches(item, changed, respect_always=False)]
    if not targets:
        return full_eligible, "full", ["no declared test path matched the changed files"]
    selected, unknown = _dependency_closure(targets, eligible)
    if unknown:
        return full_eligible, "full", unknown
    return selected, "differential", []


def alternative_backends(requirements: dict[str, Any], backend: str) -> list[dict[str, Any]]:
    """Describe other backends without silently handing work to an executor."""
    alternatives: list[dict[str, Any]] = []
    for candidate in BACKENDS:
        if candidate == backend:
            continue
        capabilities = detect(candidate)
        missing = missing_requirements(requirements, capabilities)
        alternatives.append({
            "backend": candidate,
            "status": "available" if not missing else "blocked",
            "missing": missing,
            "capabilities": capabilities,
        })
    return alternatives

def make_plan(root: pathlib.Path, inventory_path: pathlib.Path, profile: str, backend: str,
              base: str | None, diff: bool = False) -> dict[str, Any]:
    changes = discover(root, base)
    commands = load_inventory(inventory_path)
    security = load_security(inventory_path)
    effective_profile = profile
    diff_mode = "disabled"
    diff_reasons: list[str] = []
    diff_selected_names: set[str] = set()
    if diff:
        commands, diff_mode, diff_reasons = _diff_selection(
            commands, changes["changed_files"], profile, changes.get("source", "git")
        )
        diff_selected_names = {str(item.get("name", "unnamed")) for item in commands}
        if diff_mode == "full":
            effective_profile = "full"
    capabilities = detect(backend)
    selected: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    blocked: list[dict[str, Any]] = []
    changed = {item["path"] for item in changes["changed_files"]}
    fallback_to_full = diff and diff_mode == "full"
    for item in commands:
        name = str(item.get("name", "unnamed"))
        requirements = item.get("requirements", {})
        profiles = set(item.get("profiles", ["standard"]))
        supported_os = set(requirements.get("os", ["linux", "macos", "windows"]))
        if effective_profile not in profiles:
            excluded.append({"name": name, "reason": f"profile {effective_profile} is not declared"})
        elif capabilities["os"] not in supported_os:
            excluded.append({"name": name, "reason": f"backend target OS {capabilities['os']} is unsupported"})
        elif not fallback_to_full and name not in diff_selected_names and not path_matches(item, changed):
            excluded.append({"name": name, "reason": "no changed file matches manifest paths"})
        elif (missing := missing_requirements(requirements, capabilities)):
            blocked.append({
                "name": name,
                "item": item,
                "missing": missing,
                "alternatives": alternative_backends(requirements, backend),
            })
        elif backend in {"host", "windows"} and (missing_commands := missing_command_executables(item, root)):
            blocked.append({
                "name": name,
                "item": item,
                "missing": [f"command={command}" for command in missing_commands],
                "alternatives": alternative_backends(requirements, backend),
            })
        else:
            selected.append(item)
    execution_allowed = not blocked
    return {"profile": effective_profile, "requested_profile": profile, "backend": backend, "target_os": capabilities["os"], "base": base,
            "capabilities": capabilities, "changed_files": changes["changed_files"],
            "selected": selected, "excluded": excluded, "blocked": blocked,
            "security": security,
            "differential": {"enabled": diff, "mode": diff_mode, "reasons": diff_reasons},
            "execution_allowed": execution_allowed,
            "execution_blocked_reason": None if execution_allowed else "backend requirements are unavailable"}

def main() -> int:
    parser = argparse.ArgumentParser(prog="localci plan")
    parser.add_argument("--root", type=pathlib.Path, default=ROOT)
    parser.add_argument("--base")
    parser.add_argument("--diff", action="store_true", help="select a conservative test subset from changed files")
    parser.add_argument("--profile", choices=("quick", "standard", "full", "delivery"), default="standard")
    parser.add_argument("--backend", choices=("host", "docker", "act", "wsl", "windows"), default="host")
    parser.add_argument("--inventory", type=pathlib.Path, default=pathlib.Path(".localci/product-commands.json"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    inventory = args.inventory if args.inventory.is_absolute() else root / args.inventory
    try:
        plan = make_plan(root, inventory, args.profile, args.backend, args.base, args.diff)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"plan error: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    print("localCI plan")
    print(f"profile: {plan['profile']}")
    if plan["differential"]["enabled"]:
        print(f"differential: {plan['differential']['mode']}")
        for reason in plan["differential"]["reasons"]:
            print(f"  - fallback: {reason}")
    print(f"backend: {plan['backend']} (target OS: {plan['target_os']})")
    print(f"docker: {'available' if plan['capabilities']['docker'] else 'unavailable'}")
    print(f"changed files: {len(plan['changed_files'])}")
    print("selected:")
    for item in plan["selected"]:
        print(f"  - {item['name']}: {item['command']}")
    if not plan["selected"]:
        print("  - none")
    print("excluded:")
    for item in plan["excluded"]:
        print(f"  - {item['name']}: {item['reason']}")
    if not plan["excluded"]:
        print("  - none")
    print("blocked:")
    for item in plan["blocked"]:
        print(f"  - {item['name']}: missing {', '.join(item['missing'])}")
    if not plan["blocked"]:
        print("  - none")
    print(f"execution: {'allowed' if plan['execution_allowed'] else 'blocked'}")
    if plan["blocked"]:
        print("alternatives:")
        for item in plan["blocked"]:
            for alternative in item["alternatives"]:
                if alternative["status"] == "available":
                    reason = "available"
                else:
                    reason = "missing " + ", ".join(alternative["missing"])
                print(f"  - {item['name']} -> {alternative['backend']}: {reason}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
