from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from scripts import localci_doctor


class DoctorTests(unittest.TestCase):
    def test_report_includes_environment_checks_and_preserves_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "commands": [{
                    "name": "test", "stage": "test", "command": "python3 -m unittest",
                    "requirements": {"os": ["macos"], "docker": False, "services": [],
                                     "tools": ["python3"]},
                    "profiles": ["quick"], "timeout_seconds": 10,
                }],
            }), encoding="utf-8")
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "doctor@example.test"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "localCI doctor"], cwd=root, check=True)
            subprocess.run(["git", "add", "manifest.json"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
            (root / "uncommitted.txt").write_text("diagnostic fixture\n", encoding="utf-8")

            with patch.object(localci_doctor, "diagnose_backends", return_value={
                "name": "backends", "status": "ok", "summary": "1 backend(s) available",
                "available_backends": ["host"], "unavailable_backends": [],
                "backends": {"host": {"available": True}},
            }), patch.object(localci_doctor, "diagnose_docker", return_value={
                "name": "docker", "status": "warn", "summary": "Docker unavailable",
            }):
                report = localci_doctor.make_report(root, manifest, root / "history.jsonl",
                                                    min_free_gb=0)

            names = {item["name"] for item in report["checks"]}
            self.assertEqual(names, {"git", "manifest", "backends", "docker", "disk", "ci_history"})
            git_check = next(item for item in report["checks"] if item["name"] == "git")
            self.assertTrue(git_check["dirty"])
            self.assertEqual(git_check["untracked"], 1)
            self.assertEqual(report["summary"]["status"], "warn")

    def test_invalid_manifest_is_a_failure_and_reports_schema_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"schema_version": 99, "commands": []}), encoding="utf-8")
            check = localci_doctor.diagnose_manifest(root, manifest)
            self.assertEqual(check["status"], "fail")
            self.assertTrue(check["errors"])

    def test_cli_json_exposes_checks_without_running_product_commands(self):
        result = subprocess.run(
            ["bash", "localci", "doctor", "--json", "--min-free-gb", "0",
             "--history-file", "/tmp/localci-doctor-test-history.jsonl"],
            cwd=pathlib.Path(__file__).resolve().parents[1], text=True,
            capture_output=True, check=False,
        )
        self.assertIn(result.returncode, (0, 1))
        payload = json.loads(result.stdout)
        self.assertEqual({item["name"] for item in payload["checks"]},
                         {"git", "manifest", "backends", "docker", "disk", "ci_history"})


if __name__ == "__main__":
    unittest.main()
