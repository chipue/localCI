from __future__ import annotations

import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from scripts.local_executor import _docker_command, execute_plan
from scripts.execution_scope import ExecutionScope


class ExecutionIsolationTests(unittest.TestCase):
    def plan_for(self, command: str, timeout: int = 30) -> dict:
        return {
            "execution_allowed": True,
            "blocked": [],
            "backend": "host",
            "selected": [{"name": "isolated", "stage": "test", "command": command,
                          "timeout_seconds": timeout}],
        }

    def test_scope_injects_namespaced_environment_and_removes_runtime_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            logs = root / "logs"
            command = (
                "python3 -c \"import os, pathlib; p=pathlib.Path(os.environ['LOCALCI_RUNTIME_DIR']); "
                "(p/'marker').write_text(os.environ['LOCALCI_EXECUTION_ID']); "
                "print(os.environ['LOCALCI_RUN_ID'])\""
            )
            with patch.dict(os.environ, {"LOCALCI_RUNTIME_DIR": str(root / "localci-runs")}):
                result = execute_plan(self.plan_for(command), root, logs)
            item = result["results"][0]
            self.assertEqual(result["status"], "success")
            self.assertEqual(item["cleanup"], "completed")
            self.assertTrue(item["execution_id"])
            self.assertIn(result["run_id"], pathlib.Path(item["log_path"]).read_text(encoding="utf-8"))
            self.assertFalse(any((pathlib.Path(temporary) / "localci-runs").glob("**/marker")))

    def test_failure_and_timeout_are_cleaned_up(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            logs = root / "logs"
            command = (
                "python3 -c \"import os, pathlib; pathlib.Path(os.environ['LOCALCI_RUNTIME_DIR'], 'marker').write_text('x'); "
                "raise SystemExit(7)\""
            )
            with patch.dict(os.environ, {"LOCALCI_RUNTIME_DIR": str(root / "localci-runs")}):
                result = execute_plan(self.plan_for(command), root, logs)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["results"][0]["returncode"], 7)
            self.assertFalse(any((pathlib.Path(temporary) / "localci-runs").glob("**/marker")))

            with patch.dict(os.environ, {"LOCALCI_RUNTIME_DIR": str(root / "localci-runs")}):
                timeout = execute_plan(self.plan_for("python3 -c 'import time; time.sleep(10)'", 1), root, logs)
            self.assertEqual(timeout["results"][0]["status"], "timeout")
            self.assertEqual(timeout["results"][0]["cleanup"], "completed")

    def test_interrupt_is_reported_after_scope_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            with patch.dict(os.environ, {"LOCALCI_RUNTIME_DIR": str(root / "localci-runs")}), \
                    patch("scripts.execution_scope.ExecutionScope.popen", side_effect=KeyboardInterrupt):
                result = execute_plan(self.plan_for("echo never-runs"), root, root / "logs", use_cache=False)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["results"][0]["status"], "interrupted")
            self.assertFalse(list((root / "localci-runs").glob("*")))

    def test_docker_command_is_namespaced_and_ephemeral(self):
        with tempfile.TemporaryDirectory() as temporary:
            with ExecutionScope(run_id="test-run", base_dir=pathlib.Path(temporary)) as scope:
                command = _docker_command({}, "echo ok", pathlib.Path(temporary), scope)
                self.assertIn("--rm", command)
                self.assertIn("--name localci-command-", command)
                self.assertIn("--label localci.run-id=test-run", command)
                self.assertIn("--label localci.execution-id=", command)
                self.assertIn("--tmpfs /tmp", command)
                self.assertIn("LOCALCI_EXECUTION_ID", command)

    def test_docker_cleanup_is_attempted_for_registered_container(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch("scripts.execution_scope.shutil.which", return_value="/usr/bin/docker"), \
                    patch("scripts.execution_scope.subprocess.run") as run:
                with ExecutionScope(run_id="cleanup", base_dir=pathlib.Path(temporary)) as scope:
                    scope.register_container("localci-test-container")
                self.assertTrue(any(call.args[0][:4] == ["docker", "rm", "-f", "localci-test-container"]
                                    for call in run.call_args_list))


if __name__ == "__main__":
    unittest.main()
