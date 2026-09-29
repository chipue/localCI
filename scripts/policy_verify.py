#!/usr/bin/env python3
"""Verify the generic shared-CI bootstrap without third-party dependencies."""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
REQUIRED = (
    "AGENTS.md", "WORKFLOW.md", ".agent-ci-policy.yml", ".agent-ci-policy.lock",
    ".github/CI_POLICY.md", ".github/workflows/pre-merge.yml",
    ".claude/rules/ci-owner.md", ".claude/skills/run-local-ci/SKILL.md",
    ".codex/token-split-policy.json", ".cursor/rules/ci-policy.mdc",
    "scripts/ci_local.sh", "scripts/run_ci_local_quiet.sh",
)


def main() -> int:
    missing = [path for path in REQUIRED if not (ROOT / path).is_file()]
    if missing:
        print("missing policy files: " + ", ".join(missing), file=sys.stderr)
        return 2
    config = (ROOT / ".agent-ci-policy.yml").read_text(encoding="utf-8")
    if not re.search(r"^schema_version:\s*1\s*$", config, re.MULTILINE):
        print("invalid schema_version", file=sys.stderr)
        return 2
    if not re.search(r"^  enabled:\s*false\s*$", config, re.MULTILINE):
        print("github.enabled must remain false until GitHub is configured", file=sys.stderr)
        return 2
    workflow = (ROOT / ".github/workflows/pre-merge.yml").read_text(encoding="utf-8")
    if "workflow_dispatch:" not in workflow or "pull_request:" in workflow or "push:" in workflow:
        print("workflow must be manual-only during bootstrap", file=sys.stderr)
        return 2
    print("policy checks passed")
    print("product checks: not configured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
