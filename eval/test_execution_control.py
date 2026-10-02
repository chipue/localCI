from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ExecutionControlTests(unittest.TestCase):
    def run_executor(self, root: pathlib.Path, plan: dict, *extra: str) -> subprocess.CompletedProcess[str]:
        plan_path = root / "plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        return subprocess.run(
            [sys.executable, "scripts/local_executor.py", str(plan_path), "--root", str(root), "--json", *extra],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )

    def test_independent_commands_respect_max_jobs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            plan = {"execution_allowed": True, "blocked": [], "backend": "host", "selected": [
                {"name": name, "command": "python3 -c 'import time; time.sleep(0.35)'", "timeout_seconds": 10}
                for name in ("a", "b", "c")
            ]}
            started = time.monotonic()
            result = self.run_executor(root, plan, "--max-jobs", "3")
            elapsed = time.monotonic() - started
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "success")
            self.assertLess(elapsed, 0.9)
            self.assertEqual(payload["execution_summary"]["success"], 3)

    def test_dependency_waits_for_parent_in_parallel_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            marker = root / "order"
            plan = {"execution_allowed": True, "blocked": [], "backend": "host", "selected": [
                {"name": "build", "depends_on": ["test"], "command": f"echo build >> '{marker}'"},
                {"name": "test", "command": f"sleep 0.1; echo test >> '{marker}'"},
            ]}
            result = self.run_executor(root, plan, "--max-jobs", "2")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(marker.read_text(encoding="utf-8").splitlines(), ["test", "build"])

    def test_named_exclusive_resource_serializes_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            marker = root / "resource-order"
            command = f"python3 -c \"import time; p=open('{marker}','a'); p.write('start'); p.flush(); time.sleep(.2); p.write('end'); p.close()\""
            plan = {"execution_allowed": True, "blocked": [], "backend": "host", "selected": [
                {"name": "a", "command": command, "exclusive_resources": ["database"]},
                {"name": "b", "command": command, "exclusive_resources": ["database"]},
            ]}
            result = self.run_executor(root, plan, "--max-jobs", "2")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(marker.read_text(encoding="utf-8"), "startendstartend")

    def test_cancel_command_stops_running_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            control = root / "control"
            plan = {"execution_allowed": True, "blocked": [], "backend": "host", "selected": [
                {"name": "long", "command": "sleep 30", "timeout_seconds": 60}
            ]}
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            env = {"LOCALCI_CONTROL_DIR": str(control), **__import__("os").environ}
            process = subprocess.Popen(
                [sys.executable, "scripts/local_executor.py", str(plan_path), "--root", str(root), "--json"],
                cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
            )
            run_id = None
            for _ in range(50):
                states = list(control.glob("*/state.json")) if control.exists() else []
                if states:
                    run_id = states[0].parent.name
                    break
                time.sleep(0.02)
            self.assertIsNotNone(run_id)
            cancel = subprocess.run(["bash", "localci", "cancel", run_id], cwd=ROOT,
                                    env=env, text=True, capture_output=True, check=False)
            self.assertEqual(cancel.returncode, 0, cancel.stderr)
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 1, stderr)
            self.assertEqual(json.loads(stdout)["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
