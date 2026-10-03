from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts import backend_capabilities

ROOT = pathlib.Path(__file__).resolve().parents[1]


class OssBundleTests(unittest.TestCase):
    def test_act_backend_requires_the_version_in_backend_lock(self):
        with patch.object(backend_capabilities.shutil, "which", return_value="/usr/bin/act"), \
                patch.object(
                    backend_capabilities.subprocess,
                    "run",
                    return_value=SimpleNamespace(
                        returncode=0, stdout="act version 0.2.89\n", stderr=""
                    ),
                ):
            installed, expected, matches = backend_capabilities.act_version_status()
        self.assertEqual((installed, expected, matches), ("0.2.89", "0.2.89", True))

        with patch.object(backend_capabilities.shutil, "which", return_value="/usr/bin/act"), \
                patch.object(
                    backend_capabilities.subprocess,
                    "run",
                    return_value=SimpleNamespace(
                        returncode=0, stdout="act version 0.3.0\n", stderr=""
                    ),
                ):
            installed, expected, matches = backend_capabilities.act_version_status()
        self.assertEqual((installed, expected, matches), ("0.3.0", "0.2.89", False))

    def test_sample_product_manifest_is_valid(self):
        result = subprocess.run(
            [sys.executable, "scripts/validate_command_manifest.py", ".localci/sample-product-commands.json"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_sample_product_runs_through_router_and_executor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            shutil.copytree(ROOT / "examples/sample-product", root / "examples/sample-product")
            result_path = root / "run-result.json"
            history_path = root / "history.jsonl"
            result = subprocess.run(
                [
                    str(ROOT / "localci"), "run", "--root", str(root),
                    "--profile", "standard", "--backend", "host",
                    "--inventory", str(ROOT / ".localci/sample-product-commands.json"),
                    "--log-dir", str(root / "logs"), "--result-file", str(result_path),
                    "--history-file", str(history_path), "--json",
                ],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            statuses = {item["name"]: item["status"] for item in payload["result"]["results"]}
            self.assertEqual(
                statuses,
                {"sample-install": "success", "sample-test": "success",
                 "sample-typecheck": "success", "sample-build": "success"},
            )
            self.assertTrue((root / "examples/sample-product/.installed/sample_product").is_dir())
            self.assertTrue((root / "examples/sample-product/dist/sample_product.pyz").is_file())
            self.assertTrue(history_path.is_file())

    def test_run_writes_reproducible_execution_record(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            manifest = root / "commands.json"
            manifest.write_text(json.dumps({"schema_version": 1, "commands": [{
                "name": "record-check", "stage": "test", "command": "printf record-ok",
                "requirements": {"os": ["linux", "macos", "windows"], "docker": False,
                                  "services": [], "tools": []},
                "always": True, "profiles": ["quick"], "timeout_seconds": 30,
            }]}), encoding="utf-8")
            result_file = root / "result.json"
            result = subprocess.run([
                str(ROOT / "localci"), "run", "--root", str(ROOT), "--profile", "quick",
                "--backend", "host", "--inventory", str(manifest), "--log-dir", str(root / "logs"),
                "--history-file", str(root / "history.jsonl"), "--result-file", str(result_file), "--json",
            ], cwd=ROOT, text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            payload = json.loads(result.stdout)
            record_path = pathlib.Path(payload["record_file"])
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(record["execution_id"], payload["execution_id"])
            self.assertEqual(record["reproducibility"]["manifest"]["sha256"],
                             payload["reproducibility"]["manifest"]["sha256"])
            self.assertEqual(record["reproducibility"]["manifest"]["content"]["schema_version"], 1)
            self.assertTrue(record["reproducibility"]["git"]["commit"])
            self.assertEqual(record["reproducibility"]["selection"]["reason"],
                             "backend explicitly requested: host")
            command_result = record["result"]["results"][0]
            self.assertEqual(command_result["status"], "success")
            self.assertEqual(command_result["command"], "printf record-ok")
            self.assertTrue(pathlib.Path(command_result["log_path"]).is_file())

    def test_act_coverage_distinguishes_hosted_and_out_of_scope_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = pathlib.Path(temporary) / "commands.json"
            manifest.write_text(json.dumps({"commands": [
                {"name": "python", "stage": "test", "command": "python3 -m unittest", "platforms": ["linux"]},
                {"name": "missing-tool", "stage": "test", "command": "missing-localci-tool run", "platforms": ["linux"]},
                {"name": "windows", "stage": "build", "command": "powershell -File package.ps1", "platforms": ["windows"]},
            ]}), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "scripts/check_act_command_coverage.py", str(manifest), "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            classifications = {item["name"]: item["classification"] for item in json.loads(result.stdout)}
            self.assertEqual(classifications, {
                "python": "ubuntu_standard",
                "missing-tool": "dependency_required",
                "windows": "act_out_of_scope",
            })

    def test_failure_excerpt_redacts_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            log = pathlib.Path(temporary) / "failed.log"
            log.write_text("setup\nERROR token=supersecret-value\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "scripts/ci_log_excerpt.py", str(log)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("ERROR", result.stderr)
            self.assertNotIn("supersecret-value", result.stderr)


if __name__ == "__main__":
    unittest.main()
