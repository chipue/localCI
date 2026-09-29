from __future__ import annotations

import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class PolicyBootstrapTests(unittest.TestCase):
    def test_policy_verifier_passes(self):
        result = subprocess.run(
            [sys.executable, "scripts/policy_verify.py"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("product checks: not configured", result.stdout)

    def test_quiet_runner_passes_once(self):
        result = subprocess.run(
            ["bash", "scripts/run_ci_local_quiet.sh"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("policy_verify.py", result.stdout)


if __name__ == "__main__":
    unittest.main()
