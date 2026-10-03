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
    "scripts/validate_command_manifest.py", ".localci/product-commands.example.json",
    ".localci/product-commands.json",
    "scripts/discover_changes.py",
    "scripts/localci_plan.py", "localci",
    "scripts/localci_mode.py", "scripts/localci_start.py",
    "scripts/plan_gate.py",
    "scripts/local_executor.py", "scripts/security.py",
    "scripts/localci_run.py", "scripts/ci_cache.py", "scripts/localci_cache.py",
    "scripts/localci_retry.py",
    "scripts/localci_graph.py",
    "scripts/localci_matrix.py",
    "scripts/ci_history.py",
    "scripts/ci_history_store.py",
    "scripts/reproducibility.py",
    "scripts/backend_capabilities.py", "scripts/detect_backend_capabilities.py",
    "scripts/lint_product.py", "scripts/format_check.py", "scripts/security_scan.py",
    "scripts/package_product.py", "scripts/coverage_check.py",
    "scripts/integration_check.py", "scripts/smoke_check.py", "packaging.ps1",
    "scripts/backend_adapters.py", "scripts/localci_doctor.py",
    "scripts/branch_policy.py", "scripts/run_ci_scoped.py",
    "scripts/branch_register.py", ".localci/branch-parents.example.json",
    "scripts/localci_worktree.py",
    "scripts/localci_preflight.py",
    "scripts/localci_preflight_doctor.py",
    "scripts/postgres_integration_check.py",
    "scripts/service_adapters.py", "scripts/service_integration_check.py",
    ".localci/preflight/node-server-preflight.example.js",
    ".localci/preflight/node-server-preflight.example.json",
    ".localci/preflight/python-service-preflight.example.py",
    ".localci/preflight/python-service-preflight.example.json",
    ".localci/preflight/http-service-preflight.example.py",
    ".localci/preflight/http-service-preflight.example.json",
    ".localci/preflight/postgres-preflight.example.py",
    ".localci/preflight/postgres-preflight.example.json",
    "scripts/install_local_hooks.sh", ".localci/hooks/pre-commit", ".localci/hooks/pre-push",
    ".githooks/pre-push",
    "LICENSE", "oss/README.md", "oss/.agent-ci-policy.example.yml",
    "docs/maintainer.md", ".localci/backend.lock",
    ".localci/sample-product-commands.json",
    "scripts/run_ci_local_quiet.ps1", "scripts/run_command_quiet.py",
    "scripts/ci_log_excerpt.py", "scripts/install_local_hooks.ps1",
    "scripts/check_act_command_coverage.py",
    "examples/sample-product/README.md",
    "examples/sample-product/src/sample_product/__init__.py",
    "examples/sample-product/src/sample_product/__main__.py",
    "examples/sample-product/src/__main__.py",
    "examples/sample-product/tests/test_sample_product.py",
    "examples/sample-product/tools/product_stage.py",
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
    for required_input in ("local_ci_passed", "merge_authorized", "source_branch", "target_branch"):
        if required_input not in workflow:
            print(f"pre-merge workflow is missing required input: {required_input}", file=sys.stderr)
            return 2
    print("policy checks passed")
    print("product checks: delegated to localci run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
