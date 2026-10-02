from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class SecurityTests(unittest.TestCase):
    def _run(self, plan_data: dict, env: dict[str, str], root: pathlib.Path):
        plan = root / "plan.json"
        logs = root / "logs"
        plan.write_text(json.dumps(plan_data), encoding="utf-8")
        return subprocess.run(
            [sys.executable, "scripts/local_executor.py", str(plan), "--root", str(root), "--log-dir", str(logs)],
            cwd=ROOT, env=env, text=True, capture_output=True,
        ), logs

    def test_child_environment_is_allowlisted_and_log_is_redacted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            secret = "localci-test-secret-123"
            plan = {"execution_allowed": True, "blocked": [], "backend": "host",
                    "security": {"secret_env": ["LOCALCI_TEST_SECRET"]},
                    "selected": [{"name": "security", "stage": "test", "timeout_seconds": 30,
                                  "command": "python3 -c \"import os; print(os.getenv('LOCALCI_TEST_SECRET')); print(os.getenv('LOCALCI_NOT_ALLOWED'))\""}]}
            result, logs = self._run(plan, {**os.environ, "LOCALCI_TEST_SECRET": secret,
                                            "LOCALCI_NOT_ALLOWED": "not-allowed"}, root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            log = next(logs.glob("*/01-security.log"))
            content = log.read_text(encoding="utf-8")
            self.assertIn("[REDACTED]", content)
            self.assertNotIn(secret, content)
            self.assertIn("None", content)
            self.assertNotIn("not-allowed", content)

    def test_discard_policy_does_not_save_secret_log(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            plan = {"execution_allowed": True, "blocked": [], "backend": "host",
                    "security": {"secret_env": ["LOCALCI_TEST_SECRET"], "secret_log_policy": "discard"},
                    "selected": [{"name": "discard", "timeout_seconds": 30,
                                  "command": "printf '%s\\n' \"$LOCALCI_TEST_SECRET\""}]}
            result, logs = self._run(plan, {**os.environ, "LOCALCI_TEST_SECRET": "discard-me-123"}, root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(list(logs.glob("*/01-discard.log")))


if __name__ == "__main__":
    unittest.main()
