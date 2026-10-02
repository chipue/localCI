import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from scripts.github_status_report import build_report
from scripts.release_pr_gate import check


ROOT = pathlib.Path(__file__).resolve().parents[1]


class DeliveryProfileTests(unittest.TestCase):
    def test_release_gate_requires_all_explicit_inputs(self):
        passed = check(local_ci_passed="true", merge_authorized="true",
                       source_branch="feature/example", target_branch="main")
        blocked = check(local_ci_passed="true", merge_authorized="false",
                        source_branch="main", target_branch="develop")
        self.assertEqual(passed["status"], "passed")
        self.assertEqual(blocked["status"], "blocked")
        self.assertFalse(blocked["checks"]["source_is_child"])

    def test_status_report_is_stable_and_uses_execution_report(self):
        report = build_report({"status": "success", "execution_id": "run-1",
                               "plan": {"profile": "delivery"},
                               "report": {"duration_seconds": 12.5, "summary": {"success": 1}}},
                              context="localci/delivery/nightly")
        self.assertEqual(report["state"], "success")
        self.assertEqual(report["profile"], "delivery")
        self.assertEqual(report["duration_seconds"], 12.5)

        nested = build_report({"execution_id": "outer", "plan": {"profile": "delivery"},
                               "result": {"status": "success", "duration_seconds": 2.0,
                                           "execution_summary": {"success": 1}}},
                              context="localci/delivery/push")
        self.assertEqual(nested["state"], "success")
        self.assertEqual(nested["duration_seconds"], 2.0)

    def test_load_test_reproduces_300_pushes_and_cost_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            output = pathlib.Path(directory) / "load.json"
            result = subprocess.run(
                [sys.executable, "scripts/delivery_load_test.py", "--pushes", "300",
                 "--output", str(output)], cwd=ROOT, text=True, capture_output=True,
                check=True)
            payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(payload["metrics"]["pushes"], 300)
        self.assertGreaterEqual(payload["metrics"]["estimated_total_cost"], 0)
        self.assertIn("wait_seconds_p95", payload["metrics"])
        self.assertIn("detection_delay_seconds_p95", payload["metrics"])
        self.assertIn("runner_minutes", payload["metrics"])
        self.assertTrue(result.stdout.strip())

    def test_manifest_declares_delivery_profile(self):
        manifest = json.loads((ROOT / ".localci/product-commands.json").read_text(encoding="utf-8"))
        command = next(item for item in manifest["commands"] if item["name"] == "delivery-profile")
        self.assertIn("delivery", command["profiles"])
        self.assertIn("scripts/delivery_profile.py", command["command"])


if __name__ == "__main__":
    unittest.main()
