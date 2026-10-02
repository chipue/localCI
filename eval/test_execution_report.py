from __future__ import annotations

import pathlib
import tempfile
import unittest

from scripts.execution_report import build_report, render_html, write_html, write_json


class ExecutionReportTests(unittest.TestCase):
    def payload(self) -> dict:
        return {
            "execution_id": "run-example",
            "route": {"requested": "auto", "selected": "docker"},
            "plan": {"profile": "standard", "selected": [
                {"name": "install", "stage": "install"},
                {"name": "test", "stage": "test"},
            ], "blocked": []},
            "result": {
                "status": "failed", "backend": "docker",
                "backend_execution": {"modes": {"docker_container": 2},
                                       "native_commands": 2, "fallback_commands": 0},
                "execution_summary": {"total": 2, "completed": 2, "success": 1,
                                       "cached": 0, "failed": 1, "timeout": 0,
                                       "interrupted": 0, "not_run": 0,
                                       "rerun_candidates": ["test"]},
                "results": [
                    {"name": "install", "stage": "install", "status": "success",
                     "duration_seconds": 1.25},
                    {"name": "test", "stage": "test", "status": "failed",
                     "duration_seconds": 2.5, "returncode": 1,
                     "error_excerpt": "ERROR test failed", "error_highlights": [],
                     "log_path": "/tmp/test.log"},
                ],
            },
        }

    def test_report_collects_stages_failures_candidates_and_backend(self):
        report = build_report(self.payload())
        self.assertEqual(report["backend"]["selected"], "docker")
        self.assertEqual(report["duration_seconds"], 3.75)
        self.assertEqual(report["stage_stats"][1]["status"], "failed")
        self.assertEqual(report["failure_excerpts"][0]["excerpt"], "ERROR test failed")
        self.assertEqual(report["rerun_candidates"], ["test"])

    def test_reports_are_written_as_json_and_html(self):
        payload = self.payload()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            write_json(payload, root / "report.json")
            write_html(payload, root / "report.html")
            self.assertIn('"stage_stats"', (root / "report.json").read_text(encoding="utf-8"))
            html = (root / "report.html").read_text(encoding="utf-8")
            self.assertIn("docker", html)
            self.assertIn("ERROR test failed", html)
            self.assertIn("install", render_html(payload))


if __name__ == "__main__":
    unittest.main()
