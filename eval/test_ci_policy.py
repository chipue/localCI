from __future__ import annotations

import pathlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]


class PolicyBootstrapTests(unittest.TestCase):
    def test_mode_selects_lightweight_or_integration_without_git_mutation(self):
        lightweight = subprocess.run(
            ["bash", "localci", "mode", "--changed-file", "docs/note.md", "--json"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(lightweight.returncode, 0, lightweight.stderr)
        self.assertEqual(json.loads(lightweight.stdout)["mode"], "lightweight")
        integration = subprocess.run(
            ["bash", "localci", "mode", "--changed-file", ".localci/product-commands.json", "--json"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(integration.returncode, 0, integration.stderr)
        payload = json.loads(integration.stdout)
        self.assertEqual(payload["mode"], "integration")
        self.assertEqual(payload["operations"]["profile"], "full")

    def test_start_lightweight_runs_quick_profile_without_git_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            manifest = root / "manifest.json"
            commands = []
            for name, dependency in (("install", None), ("test", "install"),
                                     ("typecheck", "install"), ("build", "test")):
                item = {"name": name, "stage": name, "command": f"echo {name}",
                        "requirements": {"os": ["linux", "macos", "windows"],
                                         "docker": False, "services": [], "tools": []},
                        "always": True, "profiles": ["quick"], "timeout_seconds": 10}
                if dependency:
                    item["depends_on"] = [dependency]
                commands.append(item)
            manifest.write_text(json.dumps({"schema_version": 1, "commands": commands}),
                                encoding="utf-8")
            result = subprocess.run(
                ["bash", "localci", "start", "--root", str(root), "--inventory", str(manifest),
                 "--mode", "lightweight", "--changed-file", "docs/note.md", "--json"],
                cwd=ROOT, text=True, capture_output=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["operation_mode"], "lightweight")
            self.assertEqual(payload["selected_profile"], "quick")
            self.assertEqual(payload["mutations"], {"fetch": False, "worktree": False, "branch": False})
            self.assertEqual(payload["execution"]["result"]["status"], "success")
            self.assertEqual({item["name"] for item in payload["execution"]["result"]["results"]},
                             {"install", "test", "typecheck", "build"})

    def test_start_integration_runs_full_profile_without_git_mutation(self):
        """Integration mode must run full CI while leaving Git setup untouched."""
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            manifest = root / "manifest.json"
            commands = []
            for name, dependency in (("install", None), ("test", "install"),
                                     ("typecheck", "install"), ("build", "test")):
                item = {
                    "name": name,
                    "stage": name,
                    "command": f"printf '%s\\n' {name}",
                    "requirements": {
                        "os": ["linux", "macos", "windows"],
                        "docker": False,
                        "services": [],
                        "tools": [],
                    },
                    "always": True,
                    "profiles": ["full"],
                    "timeout_seconds": 10,
                }
                if dependency:
                    item["depends_on"] = [dependency]
                commands.append(item)
            manifest.write_text(json.dumps({"schema_version": 1, "commands": commands}),
                                encoding="utf-8")
            (root / "src").mkdir()
            (root / "src/app.py").write_text("print('fixture')\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True,
                           capture_output=True)
            subprocess.run(["git", "config", "user.email", "localci@example.test"],
                           cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "localCI test"],
                           cwd=root, check=True)
            subprocess.run(["git", "add", "manifest.json", "src/app.py"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)

            def git_state() -> tuple[str, str, str]:
                head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                                      check=True, text=True, capture_output=True).stdout.strip()
                branch = subprocess.run(["git", "branch", "--show-current"], cwd=root,
                                        check=True, text=True, capture_output=True).stdout.strip()
                worktrees = subprocess.run(["git", "worktree", "list", "--porcelain"],
                                           cwd=root, check=True, text=True,
                                           capture_output=True).stdout
                return head, branch, worktrees

            before = git_state()
            result = subprocess.run(
                ["bash", "localci", "start", "--root", str(root),
                 "--inventory", str(manifest), "--mode", "integration",
                 "--branch", "integration/fixture", "--changed-file", "src/app.py", "--json"],
                cwd=ROOT, text=True, capture_output=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["operation_mode"], "integration")
            self.assertEqual(payload["selected_profile"], "full")
            self.assertEqual(payload["mutations"],
                             {"fetch": False, "worktree": False, "branch": False})
            self.assertEqual(payload["execution"]["result"]["status"], "success")
            self.assertEqual({item["name"] for item in payload["execution"]["result"]["results"]},
                             {"install", "test", "typecheck", "build"})
            self.assertEqual(git_state(), before)

    def test_plan_selects_commands_by_changed_paths(self):
        result = subprocess.run(
            [sys.executable, "scripts/localci_plan.py", "--json",
             "--inventory", ".localci/product-commands.example.json"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        selected = {item["name"] for item in plan["selected"]}
        excluded = {item["name"] for item in plan["excluded"]}
        self.assertIn("policy-tests", selected)
        self.assertIn("workflow-act-check", excluded)
        self.assertIn("windows-package", excluded)

    def test_diff_plan_selects_matching_tests_and_declared_dependencies(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"schema_version": 1, "commands": [
                {"name": "install", "stage": "install", "command": "echo install",
                 "requirements": {"os": ["linux", "macos", "windows"], "docker": False, "services": [], "tools": []},
                 "profiles": ["standard", "full"], "timeout_seconds": 30},
                {"name": "unit", "stage": "test", "command": "echo unit",
                 "paths": ["src/**", "tests/unit/**"], "depends_on": ["install"],
                 "requirements": {"os": ["linux", "macos", "windows"], "docker": False, "services": [], "tools": []},
                 "profiles": ["standard", "full"], "timeout_seconds": 30},
                {"name": "integration", "stage": "integration_test", "command": "echo integration",
                 "paths": ["server/**"], "depends_on": ["install"],
                 "requirements": {"os": ["linux", "macos", "windows"], "docker": False, "services": [], "tools": []},
                 "profiles": ["standard", "full"], "timeout_seconds": 30},
            ]}), encoding="utf-8")
            (root / "src").mkdir()
            (root / "src/app.py").write_text("changed\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "localci@example.test"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "localCI test"], cwd=root, check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
            (root / "src/app.py").write_text("changed again\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/localci_plan.py"), "--json", "--diff",
                 "--root", str(root), "--inventory", str(manifest)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["differential"]["mode"], "differential")
            self.assertEqual([item["name"] for item in payload["selected"]], ["install", "unit"])
            self.assertEqual(payload["profile"], "standard")

    def test_diff_plan_falls_back_to_full_for_configuration_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"schema_version": 1, "commands": [
                {"name": "unit", "stage": "test", "command": "echo unit", "paths": ["src/**"],
                 "requirements": {"os": ["linux", "macos", "windows"], "docker": False, "services": [], "tools": []},
                 "profiles": ["standard", "full"], "timeout_seconds": 30},
                {"name": "coverage", "stage": "coverage", "command": "echo coverage",
                 "requirements": {"os": ["linux", "macos", "windows"], "docker": False, "services": [], "tools": []},
                 "profiles": ["full"], "timeout_seconds": 30},
            ]}), encoding="utf-8")
            (root / ".localci").mkdir()
            (root / ".localci/settings.json").write_text("{}\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "localci@example.test"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "localCI test"], cwd=root, check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
            (root / ".localci/settings.json").write_text('{"changed": true}\n', encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/localci_plan.py"), "--json", "--diff",
                 "--root", str(root), "--inventory", str(manifest)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["differential"]["mode"], "full")
            self.assertEqual(payload["profile"], "full")
            self.assertEqual({item["name"] for item in payload["selected"]}, {"unit", "coverage"})

    def test_plan_blocks_missing_tool_and_service(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = pathlib.Path(temporary) / "manifest.json"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "commands": [{
                    "name": "blocked-check",
                    "stage": "test",
                    "command": "missing-tool --check",
                    "requirements": {
                        "os": ["linux", "macos", "windows"],
                        "docker": True,
                        "services": ["postgres"],
                        "tools": ["missing-tool"],
                    },
                    "always": True,
                    "profiles": ["standard"],
                    "timeout_seconds": 60,
                }],
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "scripts/localci_plan.py", "--json", "--inventory", str(manifest)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            self.assertEqual(plan["selected"], [])
            self.assertFalse(plan["execution_allowed"])
            self.assertEqual(plan["blocked"][0]["name"], "blocked-check")
            self.assertIn("tool=missing-tool", plan["blocked"][0]["missing"])
            self.assertIn("service=postgres", plan["blocked"][0]["missing"])
            self.assertEqual({item["backend"] for item in plan["blocked"][0]["alternatives"]}, {"host", "docker", "act", "wsl", "windows"} - {plan["backend"]})
            for alternative in plan["blocked"][0]["alternatives"]:
                self.assertIn(alternative["status"], {"available", "blocked"})

    def test_plan_gate_rejects_blocked_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            plan_path = pathlib.Path(temporary) / "plan.json"
            plan_path.write_text(json.dumps({
                "execution_allowed": False,
                "blocked": [{"name": "docker-check", "missing": ["docker daemon"]}],
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "scripts/plan_gate.py", str(plan_path)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Local Executor handoff refused", result.stdout)

    def test_plan_blocks_missing_runtime_package_and_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = pathlib.Path(temporary) / "manifest.json"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "commands": [{
                    "name": "server-integration",
                    "stage": "test",
                    "command": "node server/test.js",
                    "requirements": {
                        "os": ["linux", "macos", "windows"],
                        "docker": False,
                        "services": [],
                        "tools": ["node"],
                        "packages": ["npm:localci-package-does-not-exist"],
                        "env": ["LOCALCI_REQUIRED_SERVER_SECRET"],
                    },
                    "always": True,
                    "profiles": ["standard"],
                    "timeout_seconds": 60,
                }],
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "scripts/localci_plan.py", "--json", "--inventory", str(manifest)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            self.assertEqual(plan["selected"], [])
            self.assertIn("package=npm:localci-package-does-not-exist", plan["blocked"][0]["missing"])
            self.assertIn("env=LOCALCI_REQUIRED_SERVER_SECRET", plan["blocked"][0]["missing"])

    def test_node_server_preflight_template_checks_packages_and_environment(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        with tempfile.TemporaryDirectory() as temporary:
            config = pathlib.Path(temporary) / "preflight.json"
            config.write_text(json.dumps({
                "packages": ["node:path"], "env": ["PATH"], "http": [],
            }), encoding="utf-8")
            script = ROOT / ".localci/preflight/node-server-preflight.example.js"
            passed = subprocess.run(
                [node, str(script), "--config", str(config)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(passed.returncode, 0, passed.stderr)
            config.write_text(json.dumps({
                "packages": ["localci-package-does-not-exist"], "env": ["LOCALCI_MISSING_ENV"],
                "http": [],
            }), encoding="utf-8")
            failed = subprocess.run(
                [node, str(script), "--config", str(config)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(failed.returncode, 1)
            self.assertIn("package=localci-package-does-not-exist", failed.stderr)
            self.assertIn("env=LOCALCI_MISSING_ENV", failed.stderr)

    def test_python_http_and_postgres_preflight_templates_have_safe_contracts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            python_config = root / "python.json"
            python_config.write_text(json.dumps({"packages": ["json"], "env": ["PATH"], "http": []}), encoding="utf-8")
            python_result = subprocess.run(
                [sys.executable, str(ROOT / ".localci/preflight/python-service-preflight.example.py"),
                 "--config", str(python_config)], cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(python_result.returncode, 0, python_result.stderr)

            http_config = root / "http.json"
            http_config.write_text(json.dumps({"endpoints": []}), encoding="utf-8")
            http_result = subprocess.run(
                [sys.executable, str(ROOT / ".localci/preflight/http-service-preflight.example.py"),
                 "--config", str(http_config)], cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(http_result.returncode, 0, http_result.stderr)

            postgres_config = root / "postgres.json"
            postgres_config.write_text(json.dumps({"enabled": False}), encoding="utf-8")
            postgres_result = subprocess.run(
                [sys.executable, str(ROOT / ".localci/preflight/postgres-preflight.example.py"),
                 "--config", str(postgres_config)], cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(postgres_result.returncode, 0, postgres_result.stderr)

    def test_postgres_preflight_uses_example_keys_and_validates_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            fakebin = root / "bin"
            fakebin.mkdir()
            ready_log = root / "pg_isready.log"
            psql_log = root / "psql.log"
            for name, log in (("pg_isready", ready_log), ("psql", psql_log)):
                script = fakebin / name
                script.write_text(
                    "#!/bin/sh\n"
                    f"printf '%s|%s|%s|%s|%s\\n' \"$PGHOST\" \"$PGPORT\" \"$PGDATABASE\" \"$PGUSER\" \"$PGAPPNAME\" > '{log}'\n"
                    "exit 0\n",
                    encoding="utf-8",
                )
                script.chmod(0o755)
            config = root / "postgres.json"
            config.write_text(json.dumps({
                "host": "db.example.test", "port": 15432,
                "database": "product_test", "user": "ci_user",
                "application_name": "localci-test", "query": "SELECT 1",
            }), encoding="utf-8")
            environment = os.environ.copy()
            environment["PATH"] = str(fakebin) + os.pathsep + environment["PATH"]
            result = subprocess.run(
                [sys.executable, str(ROOT / ".localci/preflight/postgres-preflight.example.py"),
                 "--config", str(config)], cwd=ROOT, env=environment,
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(ready_log.read_text(encoding="utf-8").strip(),
                             "db.example.test|15432|product_test|ci_user|localci-test")
            self.assertEqual(psql_log.read_text(encoding="utf-8").strip(),
                             "db.example.test|15432|product_test|ci_user|localci-test")

            config.write_text(json.dumps({"port": 0}), encoding="utf-8")
            invalid = subprocess.run(
                [sys.executable, str(ROOT / ".localci/preflight/postgres-preflight.example.py"),
                 "--config", str(config)], cwd=ROOT, env=environment,
                text=True, capture_output=True,
            )
            self.assertEqual(invalid.returncode, 2)
            self.assertIn("port must be 1..65535", invalid.stderr)

    def test_postgres_real_environment_integration_is_optional(self):
        with tempfile.TemporaryDirectory() as temporary:
            history = pathlib.Path(temporary) / "history.jsonl"
            result = subprocess.run(
                [sys.executable, "scripts/postgres_integration_check.py", "--json",
                 "--history-file", str(history)],
                cwd=ROOT, text=True, capture_output=True, timeout=90,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertIn(payload["status"], {"passed", "skipped"})
            self.assertTrue(history.is_file())
            record = json.loads(history.read_text(encoding="utf-8").strip())
            self.assertEqual(record["kind"], "postgres_integration")
            self.assertEqual(record["backend"], payload["provider"])
            history_view = subprocess.run(
                ["bash", "localci", "history", "--history-file", str(history),
                 "--kind", "postgres_integration", "--summary", "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(history_view.returncode, 0, history_view.stderr)
            summary = json.loads(history_view.stdout)
            self.assertEqual(summary["summary"]["records"], 1)
            self.assertIn(payload["provider"], summary["summary"]["by_backend"])
            if payload["status"] == "passed":
                self.assertEqual(payload["statuses"], ["passed", "failed", "passed"])
                self.assertEqual(payload["changed"], [False, True, True])
                self.assertEqual(record["status"], "success")

    def test_postgres_docker_provider_matches_result_contract_when_available(self):
        docker = shutil.which("docker")
        if not docker:
            self.skipTest("docker is not installed")
        if subprocess.run([docker, "info"], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode != 0:
            self.skipTest("docker daemon is unavailable")
        if subprocess.run([docker, "image", "inspect", "postgres:16-alpine"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
            self.skipTest("postgres:16-alpine image is not prepared")
        result = subprocess.run(
            [sys.executable, "scripts/postgres_integration_check.py",
             "--provider", "docker", "--json"],
            cwd=ROOT, text=True, capture_output=True, timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["provider"], "docker")
        self.assertEqual(payload["statuses"], ["passed", "failed", "passed"])
        self.assertEqual(payload["changed"], [False, True, True])

    def test_service_adapter_lifecycle_is_lazy_and_http_runs_on_demand(self):
        registry = subprocess.run(
            [sys.executable, "-c",
             "from scripts.service_adapters import inspect_adapters; import json; print(json.dumps(inspect_adapters()))"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(registry.returncode, 0, registry.stderr)
        metadata = json.loads(registry.stdout)
        self.assertEqual(set(metadata), {"http", "postgres", "redis", "mysql"})
        with tempfile.TemporaryDirectory() as temporary:
            history = pathlib.Path(temporary) / "history.jsonl"
            http = subprocess.run(
                [sys.executable, "scripts/service_integration_check.py", "--kind", "http",
                 "--history-file", str(history), "--json"],
                cwd=ROOT, text=True, capture_output=True, timeout=30,
            )
            self.assertEqual(http.returncode, 0, http.stderr)
            http_payload = json.loads(http.stdout)
            self.assertEqual(http_payload["status"], "passed")
            self.assertEqual(http_payload["lifecycle"], ["ready", "disconnected", "ready"])
            self.assertGreaterEqual(http_payload["recovery_seconds"], 0)
            history_view = subprocess.run(
                ["bash", "localci", "history", "--history-file", str(history),
                 "--kind", "service_integration", "--summary", "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(history_view.returncode, 0, history_view.stderr)
            self.assertEqual(json.loads(history_view.stdout)["summary"]["by_service"]["http"]["runs"], 1)

    def test_redis_and_mysql_docker_lifecycle_when_images_are_available(self):
        docker = shutil.which("docker")
        if not docker:
            self.skipTest("docker is not installed")
        if subprocess.run([docker, "info"], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode != 0:
            self.skipTest("docker daemon is unavailable")
        for kind, image in (("redis", "redis:7-alpine"), ("mysql", "mysql:8.4")):
            if subprocess.run([docker, "image", "inspect", image],
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode != 0:
                self.skipTest(f"{image} image is not prepared")
            with tempfile.TemporaryDirectory() as temporary:
                history = pathlib.Path(temporary) / "history.jsonl"
                result = subprocess.run(
                    [sys.executable, "scripts/service_integration_check.py",
                     "--kind", kind, "--history-file", str(history), "--json"],
                    cwd=ROOT, text=True, capture_output=True, timeout=120,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["status"], "passed")
                self.assertEqual(payload["lifecycle"], ["ready", "disconnected", "ready"])
                summary = subprocess.run(
                    ["bash", "localci", "history", "--history-file", str(history),
                     "--kind", "service_integration", "--summary", "--json"],
                    cwd=ROOT, text=True, capture_output=True,
                )
                self.assertEqual(summary.returncode, 0, summary.stderr)
                service = json.loads(summary.stdout)["summary"]["by_service"][kind]
                self.assertEqual(service["runs"], 1)
                self.assertEqual(service["passed"], 1)
                self.assertGreaterEqual(service["average_recovery_seconds"], 0)

    def test_preflight_init_copies_template_and_registers_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            manifest = root / ".localci" / "product-commands.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "commands": [{
                    "name": "server-tests", "stage": "test", "command": "node test.js",
                    "requirements": {"os": ["linux", "macos", "windows"], "docker": False,
                                      "services": [], "tools": ["node"]},
                    "profiles": ["standard"], "timeout_seconds": 60,
                }],
            }), encoding="utf-8")
            initialized = subprocess.run(
                ["bash", "localci", "preflight", "init", "--kind", "node",
                 "--root", str(root), "--command", "server-tests", "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(initialized.returncode, 0, initialized.stderr)
            payload = json.loads(initialized.stdout)
            self.assertEqual(payload["registration"], "attached:server-tests")
            self.assertTrue((root / ".localci/preflight/node-server-preflight.js").is_file())
            updated = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertIn("node .localci/preflight/node-server-preflight.js --config .localci/preflight/node-server-preflight.json",
                          updated["commands"][0]["preflight"])
            self.assertEqual(
                subprocess.run(
                    [sys.executable, "scripts/validate_command_manifest.py", str(manifest)],
                    cwd=ROOT, text=True, capture_output=True,
                ).returncode,
                0,
            )

            standalone = subprocess.run(
                ["bash", "localci", "preflight", "init", "--kind", "postgres",
                 "--root", str(root), "--force", "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(standalone.returncode, 0, standalone.stderr)
            standalone_manifest = json.loads(manifest.read_text(encoding="utf-8"))
            postgres_command = next(item for item in standalone_manifest["commands"]
                                   if item["name"] == "preflight_postgres")
            self.assertEqual(postgres_command["requirements"]["services"], ["postgres"])

    def test_preflight_doctor_reports_config_tools_and_execution(self):
        if not shutil.which("node"):
            self.skipTest("node is not installed")
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            manifest = root / ".localci" / "product-commands.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"schema_version": 1, "commands": [{
                "name": "test", "stage": "test", "command": "echo test",
                "requirements": {"os": ["linux", "macos", "windows"], "docker": False,
                                  "services": [], "tools": []},
                "profiles": ["standard"], "timeout_seconds": 30,
            }]}), encoding="utf-8")
            init = subprocess.run(
                ["bash", "localci", "preflight", "init", "--kind", "node", "--root", str(root)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(init.returncode, 0, init.stderr)
            (root / ".localci/preflight/node-server-preflight.json").write_text(
                json.dumps({"packages": ["node:path"], "env": ["PATH"], "http": []}),
                encoding="utf-8",
            )
            doctor = subprocess.run(
                ["bash", "localci", "preflight", "doctor", "--root", str(root),
                 "--kind", "node", "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(doctor.returncode, 0, doctor.stderr)
            payload = json.loads(doctor.stdout)
            self.assertEqual(payload["results"][0]["status"], "passed")
            self.assertTrue(payload["results"][0]["checks"]["tools"])
            self.assertTrue(payload["results"][0]["checks"]["execution"])

    def test_preflight_doctor_watch_emits_finite_jsonl_snapshots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            manifest = root / ".localci" / "product-commands.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({"schema_version": 1, "commands": []}),
                                encoding="utf-8")
            init = subprocess.run(
                ["bash", "localci", "preflight", "init", "--kind", "http",
                 "--root", str(root)], cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(init.returncode, 0, init.stderr)
            (root / ".localci/preflight/http-service-preflight.json").write_text(
                json.dumps({"endpoints": []}), encoding="utf-8")
            watched = subprocess.run(
                ["bash", "localci", "preflight", "doctor", "--root", str(root),
                 "--kind", "http", "--watch", "--iterations", "2", "--interval", "0",
                 "--json"], cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(watched.returncode, 0, watched.stderr)
            snapshots = [json.loads(line) for line in watched.stdout.splitlines() if line.strip()]
            self.assertEqual(len(snapshots), 2)
            self.assertEqual([item["watch"]["iteration"] for item in snapshots], [1, 2])
            self.assertEqual([item["results"][0]["status"] for item in snapshots],
                             ["passed", "passed"])
            self.assertFalse(snapshots[1]["watch"]["changed"])

    def test_plan_gate_accepts_unblocked_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            plan_path = pathlib.Path(temporary) / "plan.json"
            plan_path.write_text(json.dumps({"execution_allowed": True, "blocked": []}), encoding="utf-8")
            result = subprocess.run(
                ["bash", "localci", "gate", str(plan_path)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("plan gate: runnable", result.stdout)

    def test_executor_does_not_start_blocked_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            marker = root / "started"
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps({
                "execution_allowed": False,
                "blocked": [{"name": "blocked-check", "missing": ["docker daemon"]}],
                "selected": [{
                    "name": "must-not-run",
                    "command": f"python3 -c \"open('{marker}', 'w').write('started')\"",
                    "timeout_seconds": 30,
                }],
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "scripts/local_executor.py", str(plan_path), "--root", str(root)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("Local Executor handoff refused", result.stdout)
            self.assertFalse(marker.exists())

    def test_executor_runs_selected_command_after_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            marker = root / "started"
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps({
                "execution_allowed": True,
                "blocked": [],
                "backend": "host",
                "selected": [{
                    "name": "allowed-check",
                    "command": f"python3 -c \"open('{marker}', 'w').write('started')\"",
                    "timeout_seconds": 30,
                }],
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "scripts/local_executor.py", str(plan_path), "--root", str(root), "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(marker.exists())
            self.assertEqual(json.loads(result.stdout)["status"], "success")

    def test_executor_runs_preflight_before_product_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            marker = root / "must-not-start"
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps({
                "execution_allowed": True,
                "blocked": [],
                "backend": "host",
                "selected": [{
                    "name": "preflighted-check",
                    "command": f"touch '{marker}'",
                    "preflight": ["python3 -c 'raise SystemExit(7)'"],
                    "timeout_seconds": 30,
                }],
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "scripts/local_executor.py", str(plan_path), "--root", str(root), "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 1)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["results"][0]["status"], "failed")
            self.assertTrue(payload["results"][0]["execution_mode"].startswith("preflight_then_"))
            self.assertFalse(marker.exists())

    def test_executor_uses_backend_command_override_and_records_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            marker = root / "backend-marker"
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps({
                "execution_allowed": True,
                "blocked": [],
                "backend": "act",
                "selected": [{
                    "name": "backend-specific-check",
                    "command": "exit 99",
                    "backend_commands": {
                        "act": f"python3 -c \"open('{marker}', 'w').write('act')\"",
                    },
                    "timeout_seconds": 30,
                }],
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "scripts/local_executor.py", str(plan_path), "--root", str(root), "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(marker.exists())
            self.assertEqual(payload["results"][0]["execution_mode"], "backend_override")
            self.assertEqual(payload["backend_execution"]["native_commands"], 1)
            self.assertEqual(payload["backend_execution"]["fallback_commands"], 0)

    def test_run_connects_plan_to_executor(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = pathlib.Path(temporary) / "started"
            manifest = pathlib.Path(temporary) / "manifest.json"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "commands": [{
                    "name": "integrated-check",
                    "stage": "test",
                    "command": f"python3 -c \"open('{marker}', 'w').write('started')\"",
                    "requirements": {"os": ["linux", "macos", "windows"], "docker": False, "services": [], "tools": ["python3"]},
                    "always": True,
                    "profiles": ["standard"],
                    "timeout_seconds": 30,
                }],
            }), encoding="utf-8")
            result = subprocess.run(
                ["bash", "localci", "run", "--json", "--inventory", str(manifest)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["plan"]["selected"][0]["name"], "integrated-check")
            self.assertEqual(payload["result"]["status"], "success")
            self.assertTrue(marker.exists())

    def test_run_rejects_blocked_plan_before_executor(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = pathlib.Path(temporary) / "must-not-start"
            manifest = pathlib.Path(temporary) / "manifest.json"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "commands": [{
                    "name": "blocked-integrated-check",
                    "stage": "test",
                    "command": f"python3 -c \"open('{marker}', 'w').write('started')\"",
                    "requirements": {"os": ["linux", "macos", "windows"], "docker": False, "services": [], "tools": ["tool-that-does-not-exist"]},
                    "always": True,
                    "profiles": ["standard"],
                    "timeout_seconds": 30,
                }],
            }), encoding="utf-8")
            result = subprocess.run(
                ["bash", "localci", "run", "--json", "--inventory", str(manifest)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertIsNone(payload["result"])
            self.assertIn("Local Executor handoff refused", payload["error"])
            self.assertFalse(marker.exists())

    def test_executor_saves_full_output_and_prints_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            log_base = root / "logs"
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps({
                "execution_allowed": True,
                "blocked": [],
                "backend": "host",
                "selected": [{
                    "name": "verbose-check",
                    "command": "python3 -c \"print('full command output')\"",
                    "timeout_seconds": 30,
                }],
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "scripts/local_executor.py", str(plan_path), "--root", str(root), "--log-dir", str(log_base)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("executor: success", result.stdout)
            self.assertNotIn("full command output", result.stdout)
            log_file = next(next(log_base.iterdir()).glob("01-verbose-check.log"))
            self.assertIn("full command output", log_file.read_text(encoding="utf-8"))

    def test_run_reports_log_paths_in_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            marker = root / "started"
            log_base = root / "logs"
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "commands": [{
                    "name": "logged-check",
                    "stage": "test",
                    "command": f"python3 -c \"print('saved'); open('{marker}', 'w').write('ok')\"",
                    "requirements": {"os": ["linux", "macos", "windows"], "docker": False, "services": [], "tools": ["python3"]},
                    "always": True,
                    "profiles": ["standard"],
                    "timeout_seconds": 30,
                }],
            }), encoding="utf-8")
            result = subprocess.run(
                ["bash", "localci", "run", "--json", "--log-dir", str(log_base), "--inventory", str(manifest)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            log_path = pathlib.Path(payload["result"]["results"][0]["log_path"])
            self.assertTrue(log_path.is_file())
            self.assertIn("saved", log_path.read_text(encoding="utf-8"))
            self.assertTrue(marker.exists())

    def test_real_manifest_runs_through_retry_and_graph(self):
        if os.environ.get("LOCALCI_EXECUTOR_RUN") == "1":
            self.skipTest("avoid recursively invoking the end-to-end localCI test")
        with tempfile.TemporaryDirectory() as temporary:
            result_file = pathlib.Path(temporary) / "run-result.json"
            log_dir = pathlib.Path(temporary) / "logs"
            run_result = subprocess.run(
                ["bash", "localci", "run", "--json", "--result-file", str(result_file), "--log-dir", str(log_dir)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(run_result.returncode, 0, run_result.stdout + run_result.stderr)
            self.assertTrue(result_file.is_file())
            payload = json.loads(result_file.read_text(encoding="utf-8"))
            self.assertEqual(
                [item["name"] for item in payload["plan"]["selected"]],
                ["install", "test", "typecheck", "build", "lint", "format_check", "security", "smoke_test"],
            )
            self.assertEqual(payload["result"]["status"], "success")

            retry_result = subprocess.run(
                ["bash", "localci", "retry", str(result_file), "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(retry_result.returncode, 0, retry_result.stdout + retry_result.stderr)
            self.assertEqual(json.loads(retry_result.stdout)["result"]["status"], "no_candidates")

            graph_result = subprocess.run(
                ["bash", "localci", "graph", str(result_file), "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(graph_result.returncode, 0, graph_result.stdout + graph_result.stderr)
            graph = json.loads(graph_result.stdout)
            self.assertEqual(graph["retry_order"], [])
            self.assertEqual(graph["bottleneck_stage"]["name"], "test")

    def test_real_manifest_full_profile_plan_is_complete(self):
        if os.environ.get("LOCALCI_EXECUTOR_RUN") == "1":
            self.skipTest("backend-specific full-plan assertion runs outside generated act jobs")
        result = subprocess.run(
            ["bash", "localci", "plan", "--profile", "full", "--json"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        selected = {item["name"] for item in payload["selected"]}
        blocked = {item["name"] for item in payload["blocked"]}
        excluded = {item["name"] for item in payload["excluded"]}
        self.assertIn("full_ci", selected)
        self.assertIn("workflow-act-check", selected | blocked | excluded)
        self.assertIn("windows-package", excluded)

    def test_real_manifest_matrix_reports_all_local_backends(self):
        result = subprocess.run(
            ["bash", "localci", "matrix", "--profile", "standard", "--json"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        matrix = json.loads(result.stdout)
        self.assertEqual(set(matrix["backends"]), {"host", "docker", "act", "wsl", "windows"})
        self.assertTrue(matrix["backends"]["host"]["execution_allowed"])
        self.assertFalse(matrix["backends"]["windows"]["runtime_available"])
        self.assertTrue(any("backend runtime=windows" in reason
                            for item in matrix["backends"]["windows"]["blocked"]
                            for reason in item["missing"]))
        self.assertIsInstance(matrix["backends"]["host"]["tools"], dict)
        self.assertIn("act", matrix["backends"]["act"]["tools"])

    def test_doctor_separates_core_and_optional_backends(self):
        result = subprocess.run(
            ["bash", "localci", "doctor", "--json"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["core"], ["host"])
        self.assertEqual(set(report["optional"]), {"docker", "act", "wsl", "windows"})
        self.assertIn("host", report["available_backends"])
        self.assertTrue(set(report["backends"]).issubset(set(report["available_backends"])))

        all_result = subprocess.run(
            ["bash", "localci", "doctor", "--all", "--json"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(all_result.returncode, 0, all_result.stdout + all_result.stderr)
        all_report = json.loads(all_result.stdout)
        self.assertEqual(set(all_report["backends"]), {"host", "docker", "act", "wsl", "windows"})

    def test_branch_register_rejects_self_and_cycles(self):
        from scripts.branch_register import find_cycle, validate_registration

        with self.assertRaisesRegex(ValueError, "own parent"):
            validate_registration({}, "feature/a", "feature/a")
        with self.assertRaisesRegex(ValueError, "cycle"):
            validate_registration({"feature/a": "integration/a"}, "integration/a", "feature/a")
        self.assertIsNone(find_cycle({"feature/a": "integration/a", "feature/b": "feature/a"}))

        result = subprocess.run(
            ["bash", "localci", "branch", "register", "--parent", "does-not-exist",
             "--branch", "feature/invalid-registration"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("branch register failed", result.stderr)

    def test_branch_register_rejects_non_ancestor_without_writing(self):
        import scripts.branch_register as branch_register

        with tempfile.TemporaryDirectory() as temporary:
            parents_file = pathlib.Path(temporary) / "branch-parents.json"

            def fake_git(command, *args, **kwargs):
                if command[:2] == ["git", "merge-base"]:
                    return SimpleNamespace(returncode=1)
                return SimpleNamespace(returncode=0)

            with patch.object(branch_register, "PARENTS_FILE", parents_file), \
                    patch.object(branch_register.subprocess, "run", side_effect=fake_git):
                with patch.object(sys, "argv", [
                    "localci branch register",
                    "--parent", "integration/other",
                    "--branch", "feature/router",
                ]):
                    self.assertEqual(branch_register.main(), 2)

            self.assertFalse(parents_file.exists())

    def test_branch_register_and_merge_check_use_registered_parent(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = pathlib.Path(temporary)
            parents_file = temporary_root / "branch-parents.json"
            current_branch = subprocess.run(
                ["git", "branch", "--show-current"], cwd=ROOT,
                check=True, text=True, capture_output=True,
            ).stdout.strip()
            if current_branch == "main":
                self.skipTest("branch registration integration test requires a child branch")
            environment = {
                **os.environ,
                "LOCALCI_BRANCH_PARENTS_FILE": str(parents_file),
                "AGENT_CI_BACKEND": "host",
                "AGENT_CI_LOG_DIR": str(temporary_root / "logs"),
                "AGENT_CI_SHARED_DIR": str(temporary_root / "shared"),
            }

            register = subprocess.run(
                ["bash", "localci", "branch", "register",
                 "--parent", "main", "--branch", current_branch],
                cwd=ROOT, env=environment, text=True, capture_output=True,
            )
            self.assertEqual(register.returncode, 0, register.stdout + register.stderr)
            self.assertIn(f"registered parent: {current_branch} <- main", register.stdout)
            self.assertEqual(
                json.loads(parents_file.read_text(encoding="utf-8")),
                {current_branch: "main"},
            )

            merge_check = subprocess.run(
                ["bash", "localci", "merge-check", "--profile", "quick", "--backend", "host"],
                cwd=ROOT, env=environment, text=True, capture_output=True,
            )
            self.assertEqual(merge_check.returncode, 0, merge_check.stdout + merge_check.stderr)
            expected_output = (
                "CI delegated to current localCI executor"
                if environment.get("LOCALCI_EXECUTOR_RUN") == "1"
                else "CI passed (policy + product;"
            )
            self.assertIn(expected_output, merge_check.stdout)

    def test_nested_branch_registration_scopes_merge_check_to_each_parent(self):
        import scripts.branch_policy as branch_policy
        import scripts.branch_register as branch_register
        import scripts.run_ci_scoped as run_ci_scoped

        levels = [
            ("integration/level-one", "main"),
            ("feature/level-two", "integration/level-one"),
            ("work/level-three", "feature/level-two"),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            parents_file = pathlib.Path(temporary) / "branch-parents.json"

            def fake_git(*args, **kwargs):
                return SimpleNamespace(returncode=0)

            with patch.object(branch_register, "PARENTS_FILE", parents_file), \
                    patch.object(branch_register.subprocess, "run", side_effect=fake_git):
                for child, parent in levels:
                    with patch.object(sys, "argv", [
                        "localci branch register", "--parent", parent, "--branch", child,
                    ]):
                        self.assertEqual(branch_register.main(), 0)

            self.assertEqual(
                json.loads(parents_file.read_text(encoding="utf-8")),
                dict(levels),
            )

            captured: list[tuple[str, dict[str, str]]] = []

            def fake_ci(command, **kwargs):
                captured.append((command[-1], kwargs["env"]))
                return SimpleNamespace(returncode=0)

            with patch.object(branch_policy, "PARENTS_FILE", parents_file), \
                    patch.object(branch_policy, "is_ancestor", return_value=True), \
                    patch.object(run_ci_scoped.subprocess, "run", side_effect=fake_ci):
                for child, parent in levels:
                    with patch.object(run_ci_scoped, "current_branch", return_value=child), \
                            patch.object(branch_policy, "current_branch", return_value=child), \
                            patch.object(sys, "argv", [
                                "localci merge-check", "--profile", "quick", "--backend", "host",
                            ]):
                        self.assertEqual(run_ci_scoped.main(), 0)

            self.assertEqual([env["AGENT_CI_BASE"] for _, env in captured], [
                "main", "integration/level-one", "feature/level-two",
            ])
            self.assertEqual([env["AGENT_CI_PROFILE"] for _, env in captured], ["quick"] * 3)
            self.assertEqual([env["AGENT_CI_BACKEND"] for _, env in captured], ["host"] * 3)

    def test_worktree_promote_preserves_dirty_files_and_installs_hooks(self):
        from scripts.localci_worktree import promote

        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary) / "product"
            root.mkdir()
            (root / "scripts").mkdir()
            (root / ".localci" / "hooks").mkdir(parents=True)
            (root / "scripts" / "install_local_hooks.sh").write_text(
                "#!/usr/bin/env bash\nset -euo pipefail\ngit config core.hooksPath .localci/hooks\n",
                encoding="utf-8",
            )
            for hook in ("pre-commit", "pre-push"):
                (root / ".localci" / "hooks" / hook).write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            (root / "tracked.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)
            subprocess.run(["git", "switch", "-qc", "feature/promote"], cwd=root, check=True)
            (root / "tracked.txt").write_text("dirty tracked\n", encoding="utf-8")
            (root / "untracked.txt").write_text("dirty untracked\n", encoding="utf-8")

            history_file = root / "history.jsonl"
            history_file.write_text(json.dumps({"status": "success"}) + "\n", encoding="utf-8")
            registry = root / "registry.json"
            target = pathlib.Path(temporary) / "formal-worktree"
            result = promote(root, history_file, registry, target, "main", 1)

            self.assertEqual(result["status"], "promoted")
            self.assertTrue(result["source_preserved"])
            self.assertTrue(result["temporary_snapshot"])
            self.assertEqual((target / "tracked.txt").read_text(encoding="utf-8"), "dirty tracked\n")
            self.assertEqual((target / "untracked.txt").read_text(encoding="utf-8"), "dirty untracked\n")
            self.assertEqual(
                json.loads((target / ".localci" / "branch-parents.json").read_text(encoding="utf-8")),
                {"worktree/feature/promote": "feature/promote"},
            )
            self.assertEqual(
                json.loads(registry.read_text(encoding="utf-8"))["worktrees"][0]["formal_branch"],
                "worktree/feature/promote",
            )

            worktree_list = subprocess.run(
                ["git", "worktree", "list", "--porcelain"], cwd=root,
                text=True, capture_output=True, check=True,
            ).stdout
            self.assertIn(str(target), worktree_list)

    def test_worktree_list_calculates_generation_placement_and_merge_state(self):
        from scripts.localci_worktree import build_listing, render_tree, retire_candidates

        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary) / "product"
            root.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            (root / "state.txt").write_text("main\n", encoding="utf-8")
            subprocess.run(["git", "add", "state.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "main"], cwd=root, check=True)
            subprocess.run(["git", "switch", "-qc", "integration/one"], cwd=root, check=True)
            (root / "state.txt").write_text("integration\n", encoding="utf-8")
            subprocess.run(["git", "commit", "-qam", "integration"], cwd=root, check=True)
            subprocess.run(["git", "switch", "-qc", "feature/two"], cwd=root, check=True)
            (root / "state.txt").write_text("feature\n", encoding="utf-8")
            subprocess.run(["git", "commit", "-qam", "feature"], cwd=root, check=True)
            mapping_path = root / ".localci" / "branch-parents.json"
            mapping_path.parent.mkdir()
            mapping_path.write_text(json.dumps({
                "integration/one": "main",
                "feature/two": "integration/one",
            }), encoding="utf-8")
            registry = root / "registry.json"
            registry.write_text(json.dumps({"worktrees": [{
                "branch": "feature/two", "formal_branch": "worktree/feature/two",
                "path": str(root.parent / "formal-feature-two"),
            }]}), encoding="utf-8")

            listing = build_listing(root, registry)
            nodes = {item["branch"]: item for item in listing["branches"]}
            self.assertEqual(nodes["main"]["generation"], 0)
            self.assertEqual(nodes["integration/one"]["generation"], 1)
            self.assertEqual(nodes["feature/two"]["generation"], 2)
            self.assertEqual(nodes["feature/two"]["placement"], "formal")
            self.assertEqual(nodes["feature/two"]["formal_branch"], "worktree/feature/two")
            self.assertEqual(nodes["feature/two"]["integration_status"], "unmerged")
            tree = render_tree(listing)
            self.assertIn("main [gen=0, root", tree)
            self.assertIn("└─ integration/one [gen=1, unmerged", tree)
            self.assertIn("└─ feature/two [gen=2, unmerged, placement=formal", tree)

            result = subprocess.run(
                ["bash", str(ROOT / "localci"), "worktree", "list",
                 "--root", str(root), "--registry", str(registry), "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(json.loads(result.stdout)["branches"]), 3)

            formal_branch = "worktree/feature/two"
            formal_path = root.parent / "formal-feature-two"
            subprocess.run(["git", "branch", formal_branch, "feature/two"], cwd=root, check=True)
            subprocess.run(["git", "worktree", "add", "-q", str(formal_path), formal_branch], cwd=root, check=True)
            subprocess.run(["git", "branch", "-f", "integration/one", "feature/two"], cwd=root, check=True)
            refreshed = retire_candidates(root, registry)
            self.assertEqual(len(refreshed), 1)
            self.assertEqual(refreshed[0]["integration_status"], "merged")
            self.assertEqual(refreshed[0]["rechecks"][0]["branch"], "integration/one")
            self.assertEqual(refreshed[0]["rechecks"][0]["base"], "main")
            self.assertEqual(
                refreshed[0]["rechecks"][0]["command"],
                "localci merge-check --base main --profile full",
            )
            listed = subprocess.run(
                ["bash", str(ROOT / "localci"), "worktree", "list", "--root", str(root),
                 "--registry", str(registry), "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(listed.returncode, 0, listed.stderr)
            listing_payload = json.loads(listed.stdout)
            self.assertEqual(listing_payload["deletion_candidates"][0]["branch"], "feature/two")
            self.assertTrue(listing_payload["deletion_candidates"][0]["checks"]["clean"])
            self.assertTrue(listing_payload["deletion_candidates"][0]["safe_to_remove"])

    def test_worktree_list_reports_dirty_duplicate_and_stale_metadata(self):
        from scripts.localci_worktree import build_listing

        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary) / "product"
            root.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            (root / "state.txt").write_text("main\n", encoding="utf-8")
            subprocess.run(["git", "add", "state.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "main"], cwd=root, check=True)
            subprocess.run(["git", "switch", "-qc", "feature/dirty"], cwd=root, check=True)
            (root / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")
            registry = root / "registry.json"
            missing_path = root.parent / "missing-formal-worktree"
            registry.write_text(json.dumps({"worktrees": [
                {"branch": "feature/dirty", "formal_branch": "worktree/missing",
                 "path": str(missing_path)},
                {"branch": "feature/dirty", "formal_branch": "worktree/missing-duplicate",
                 "path": str(missing_path)},
            ]}), encoding="utf-8")

            listing = build_listing(root, registry)
            diagnostics = listing["diagnostics"]
            self.assertEqual(diagnostics["dirty_worktrees"][0]["branch"], "feature/dirty")
            self.assertEqual(diagnostics["dirty_worktrees"][0]["state"], "dirty")
            self.assertEqual(diagnostics["duplicate_branches"],
                             [{"branch": "feature/dirty", "count": 2}])
            self.assertEqual(len(diagnostics["stale_registry"]), 2)
            self.assertIn("path_missing", diagnostics["stale_registry"][0]["reasons"])
            self.assertIn("formal_branch_missing", diagnostics["stale_registry"][0]["reasons"])
            self.assertTrue(any("dirty worktrees" in warning for warning in diagnostics["warnings"]))
            self.assertTrue(any("stale registry records" in warning for warning in diagnostics["warnings"]))

            result = subprocess.run(
                ["bash", str(ROOT / "localci"), "worktree", "list", "--root", str(root),
                 "--registry", str(registry), "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("diagnostics", json.loads(result.stdout))

    def test_worktree_retire_previews_and_removes_merged_formal_worktree(self):
        from scripts.localci_worktree import retire

        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary) / "product"
            root.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            (root / "state.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "state.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)
            subprocess.run(["git", "switch", "-qc", "feature/retire"], cwd=root, check=True)
            formal_branch = "worktree/feature/retire"
            target = pathlib.Path(temporary) / "formal"
            subprocess.run(["git", "branch", formal_branch, "feature/retire"], cwd=root, check=True)
            subprocess.run(["git", "worktree", "add", "-q", str(target), formal_branch], cwd=root, check=True)
            mapping = root / ".localci" / "branch-parents.json"
            mapping.parent.mkdir()
            mapping.write_text(json.dumps({"feature/retire": "main"}), encoding="utf-8")
            registry = root / "registry.json"
            registry.write_text(json.dumps({"worktrees": [{
                "branch": "feature/retire", "formal_branch": formal_branch,
                "path": str(target),
            }]}), encoding="utf-8")

            preview = retire(root, registry, apply=False, force=False, delete_branch=False)
            self.assertEqual(preview["status"], "planned")
            self.assertEqual(preview["candidates"][0]["branch"], "feature/retire")
            self.assertTrue(all(preview["candidates"][0]["checks"].values()))
            self.assertEqual(preview["candidates"][0]["rechecks"], [])
            self.assertEqual(preview["recheck_warnings"], [])
            self.assertTrue(target.exists())

            applied = retire(root, registry, apply=True, force=False, delete_branch=True,
                             history_file=root / "history.jsonl")
            self.assertEqual(applied["status"], "retired")
            self.assertEqual(applied["retired"][0]["formal_branch"], formal_branch)
            self.assertFalse(target.exists())
            self.assertEqual(json.loads(registry.read_text(encoding="utf-8"))["worktrees"], [])
            history_records = [json.loads(line) for line in
                               (root / "history.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(history_records[0]["kind"], "worktree_retire")
            self.assertEqual(history_records[0]["status"], "retired")
            self.assertEqual(history_records[0]["worktree_retirement"]["retired"][0]["branch"], "feature/retire")
            self.assertEqual(history_records[0]["worktree_retirement"]["recheck_results"], [])
            self.assertEqual(history_records[0]["worktree_retirement"]["candidates"][0]["checks"]["clean"], True)
            branch_check = subprocess.run(["git", "rev-parse", "--verify", formal_branch], cwd=root)
            self.assertNotEqual(branch_check.returncode, 0)

    def test_run_reports_backend_only_in_completion_line(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "commands": [{
                    "name": "smoke", "stage": "test", "command": "echo ok",
                    "requirements": {"os": ["macos"], "docker": False,
                                      "services": [], "tools": []},
                    "always": True, "profiles": ["standard"], "timeout_seconds": 30,
                }],
            }), encoding="utf-8")
            result = subprocess.run(
                ["bash", "localci", "run", "--backend", "host", "--root", str(root),
                 "--inventory", str(manifest)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("route:", result.stdout)
            self.assertNotIn("backend: host", result.stdout.split("execution:", 1)[0])
            self.assertIn("execution: success (backend: host)", result.stdout)

    def test_synthetic_matrix_and_history_compare_os_and_fallbacks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            manifest = root / "matrix-manifest.json"
            history = root / "history.jsonl"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "commands": [
                    {
                        "name": "portable-check", "stage": "test", "command": "exit 0",
                        "requirements": {"os": ["linux", "macos", "windows"], "docker": False,
                                          "services": [], "tools": []},
                        "always": True, "profiles": ["standard"], "timeout_seconds": 30,
                    },
                    {
                        "name": "windows-only", "stage": "build",
                        "command": "powershell -File packaging.ps1",
                        "requirements": {"os": ["windows"], "docker": False,
                                          "services": [], "tools": ["powershell"]},
                        "always": True, "profiles": ["standard"], "timeout_seconds": 30,
                    },
                ],
            }), encoding="utf-8")
            matrix = subprocess.run(
                ["bash", "localci", "matrix", "--profile", "standard", "--json",
                 "--inventory", str(manifest), "--root", str(root)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(matrix.returncode, 0, matrix.stdout + matrix.stderr)
            matrix_payload = json.loads(matrix.stdout)["backends"]
            self.assertEqual(matrix_payload["host"]["selected"], ["portable-check"])
            if matrix_payload["act"]["runtime_available"]:
                self.assertEqual(matrix_payload["act"]["selected"], ["portable-check"])
            else:
                self.assertIn("portable-check", {item["name"] for item in matrix_payload["act"]["blocked"]})
            self.assertEqual(matrix_payload["windows"]["selected"], [])
            self.assertIn("windows-only", {item["name"] for item in matrix_payload["windows"]["blocked"]})
            self.assertIn("windows-only", {item["name"] for item in matrix_payload["host"]["excluded"]})

            history.write_text(
                json.dumps({"execution_id": "host-run", "kind": "run", "backend": "host",
                            "status": "success", "duration_seconds": 10,
                            "fallback_commands": 0,
                            "execution_summary": {"total": 1}}) + "\n"
                + json.dumps({"execution_id": "act-run", "kind": "run", "backend": "act",
                              "status": "success", "duration_seconds": 1,
                              "fallback_commands": 1,
                              "execution_summary": {"total": 1}}) + "\n",
                encoding="utf-8",
            )
            summary = subprocess.run(
                ["bash", "localci", "history", "--summary", "--json",
                 "--history-file", str(history)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(summary.returncode, 0, summary.stdout + summary.stderr)
            aggregate = json.loads(summary.stdout)["summary"]["by_backend"]
            self.assertEqual(aggregate["host"]["fallback_rate"], 0.0)
            self.assertEqual(aggregate["act"]["fallback_rate"], 1.0)

            routed = subprocess.run(
                ["bash", "localci", "run", "--backend", "auto", "--json",
                 "--inventory", str(manifest), "--root", str(root),
                 "--history-file", str(history)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(routed.returncode, 0, routed.stdout + routed.stderr)
            route_payload = json.loads(routed.stdout)
            self.assertEqual(route_payload["route"]["selected"], "host")
            candidates = {item["backend"]: item for item in route_payload["route"]["candidates"]}
            self.assertGreater(candidates["act"]["score"], candidates["host"]["score"])

    def test_auto_route_selects_host_for_real_manifest(self):
        if os.environ.get("LOCALCI_EXECUTOR_RUN") == "1":
            self.skipTest("avoid recursively invoking the auto-route end-to-end test")
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
            ["bash", "localci", "run", "--backend", "auto", "--json", "--history-file", str(pathlib.Path(temporary) / "history.jsonl"), "--log-dir", str(pathlib.Path(temporary) / "logs")],
            cwd=ROOT, text=True, capture_output=True,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["route"]["requested"], "auto")
        self.assertEqual(payload["route"]["selected"], "host")
        self.assertEqual(payload["route"]["status"], "selected")

    def test_auto_route_keeps_all_blocked_diagnostics(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = pathlib.Path(temporary) / "windows-only.json"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "commands": [{
                    "name": "windows-only", "stage": "test", "command": "echo should-not-run",
                    "requirements": {"os": ["windows"], "docker": False, "services": [], "tools": []},
                    "always": True, "profiles": ["standard"], "timeout_seconds": 30,
                }],
            }), encoding="utf-8")
            result = subprocess.run(
                ["bash", "localci", "run", "--backend", "auto", "--json", "--inventory", str(manifest)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["route"]["status"], "blocked")
            self.assertIsNone(payload["route"]["selected"])
            self.assertEqual({item["backend"] for item in payload["route"]["candidates"]}, {"host", "docker", "act", "wsl", "windows"})
            self.assertIsNone(payload["result"])

    def test_windows_manifest_is_safely_blocked_and_recorded_without_windows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            manifest = root / "windows-manifest.json"
            history = root / "history.jsonl"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "commands": [{
                    "name": "windows-package",
                    "stage": "build",
                    "command": "powershell -File packaging.ps1",
                    "requirements": {
                        "os": ["windows"],
                        "docker": False,
                        "services": [],
                        "tools": ["powershell"],
                    },
                    "always": True,
                    "profiles": ["standard"],
                    "timeout_seconds": 30,
                }],
            }), encoding="utf-8")
            plan = subprocess.run(
                ["bash", "localci", "plan", "--backend", "windows", "--json",
                 "--inventory", str(manifest), "--root", str(root)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
            plan_payload = json.loads(plan.stdout)
            self.assertFalse(plan_payload["execution_allowed"])
            self.assertEqual(plan_payload["selected"], [])
            self.assertEqual(plan_payload["blocked"][0]["name"], "windows-package")
            self.assertIn("backend runtime=windows", plan_payload["blocked"][0]["missing"])
            self.assertIn("tool=powershell", plan_payload["blocked"][0]["missing"])

            run = subprocess.run(
                ["bash", "localci", "run", "--backend", "windows", "--json",
                 "--inventory", str(manifest), "--root", str(root),
                 "--history-file", str(history)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
            payload = json.loads(run.stdout)
            self.assertIsNone(payload["result"])
            self.assertEqual(payload["route"]["selected"], "windows")
            records = json.loads(
                subprocess.run(
                    ["bash", "localci", "history", "--history-file", str(history), "--json"],
                    cwd=ROOT, text=True, capture_output=True, check=True,
                ).stdout
            )["records"]
            self.assertEqual(records[0]["backend"], "windows")
            self.assertEqual(records[0]["status"], "blocked")
            self.assertEqual(records[0]["route_status"], "selected")

            auto = subprocess.run(
                ["bash", "localci", "run", "--backend", "auto", "--json",
                 "--inventory", str(manifest), "--root", str(root),
                 "--history-file", str(root / "auto-history.jsonl")],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(auto.returncode, 2, auto.stdout + auto.stderr)
            auto_payload = json.loads(auto.stdout)
            self.assertEqual(auto_payload["route"]["status"], "blocked")
            self.assertIsNone(auto_payload["route"]["selected"])
            self.assertEqual(
                {candidate["backend"] for candidate in auto_payload["route"]["candidates"]},
                {"host", "docker", "act", "wsl", "windows"},
            )
            auto_records = json.loads(
                subprocess.run(
                    ["bash", "localci", "history", "--history-file", str(root / "auto-history.jsonl"), "--json"],
                    cwd=ROOT, text=True, capture_output=True, check=True,
                ).stdout
            )["records"]
            self.assertEqual(auto_records[0]["backend"], "unrouted")
            self.assertEqual(set(auto_records[0]["blocked_backends"]), {"host", "docker", "act", "wsl", "windows"})

    def test_windows_package_command_has_packaging_script(self):
        manifest = json.loads((ROOT / ".localci/product-commands.json").read_text(encoding="utf-8"))
        command = next(item for item in manifest["commands"] if item["name"] == "windows-package")
        self.assertEqual(command["command"], "powershell -File packaging.ps1")
        self.assertTrue((ROOT / "packaging.ps1").is_file())
        packaging = (ROOT / "packaging.ps1").read_text(encoding="utf-8")
        self.assertIn("Compress-Archive", packaging)
        self.assertIn("Required package input is missing", packaging)

    def test_blocked_windows_case_flows_through_retry_and_mermaid_graph(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            manifest = root / "windows-manifest.json"
            result_file = root / "blocked-result.json"
            history = root / "history.jsonl"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "commands": [{
                    "name": "windows-package", "stage": "build",
                    "command": "powershell -File packaging.ps1",
                    "requirements": {"os": ["windows"], "docker": False,
                                      "services": [], "tools": ["powershell"]},
                    "always": True, "profiles": ["standard"], "timeout_seconds": 30,
                }],
            }), encoding="utf-8")
            run = subprocess.run(
                ["bash", "localci", "run", "--backend", "windows", "--json",
                 "--inventory", str(manifest), "--root", str(root),
                 "--result-file", str(result_file), "--history-file", str(history)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(run.returncode, 2, run.stdout + run.stderr)

            graph = subprocess.run(
                ["bash", "localci", "graph", str(result_file), "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(graph.returncode, 0, graph.stdout + graph.stderr)
            graph_payload = json.loads(graph.stdout)
            self.assertEqual(graph_payload["blocked_candidates"], ["windows-package"])
            self.assertEqual(graph_payload["retry_order"], ["windows-package"])
            self.assertEqual(graph_payload["nodes"][0]["status"], "blocked")

            mermaid = subprocess.run(
                ["bash", "localci", "graph", str(result_file), "--mermaid"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(mermaid.returncode, 0, mermaid.stdout + mermaid.stderr)
            self.assertIn("status_blocked_rerun", mermaid.stdout)
            self.assertIn("blocked=1", mermaid.stdout)

            retry = subprocess.run(
                ["bash", "localci", "retry", str(result_file), "--json",
                 "--root", str(root), "--history-file", str(history)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
            retry_payload = json.loads(retry.stdout)
            self.assertEqual(retry_payload["retry_plan"]["retry_order"], ["windows-package"])
            self.assertEqual(retry_payload["result"]["status"], "blocked")
            self.assertEqual(retry_payload["result"]["results"][0]["reason"],
                             "backend_requirements_unavailable")

    def test_auto_route_uses_history_score(self):
        if os.environ.get("LOCALCI_EXECUTOR_RUN") == "1":
            self.skipTest("avoid nested route selection inside an act container")
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            manifest = root / "manifest.json"
            history = root / "history.jsonl"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "commands": [{
                    "name": "portable-check", "stage": "test", "command": "exit 0",
                    "requirements": {"os": ["linux", "macos", "windows"], "docker": False, "services": [], "tools": []},
                    "always": True, "profiles": ["standard"], "timeout_seconds": 30,
                }],
            }), encoding="utf-8")
            history.write_text(json.dumps({"execution_id": "old-host", "kind": "run", "backend": "host", "status": "failed", "duration_seconds": 1}) + "\n"
                                + json.dumps({"execution_id": "old-act", "kind": "run", "backend": "act", "status": "success", "duration_seconds": 3}) + "\n", encoding="utf-8")
            result = subprocess.run(
                ["bash", "localci", "run", "--backend", "auto", "--json", "--history-file", str(history), "--inventory", str(manifest), "--root", str(root)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["route"]["selected"], "act")
            scores = {item["backend"]: item["score"] for item in payload["route"]["candidates"]}
            self.assertGreater(scores["host"], scores["act"])

    def test_retry_and_graph_preserve_backend_route(self):
        with tempfile.TemporaryDirectory() as temporary:
            result_path = pathlib.Path(temporary) / "result.json"
            result_path.write_text(json.dumps({
                "route": {"requested": "auto", "selected": "host", "status": "selected"},
                "plan": {"backend": "host", "selected": [{
                    "name": "retryable", "stage": "test", "command": "exit 0", "timeout_seconds": 30,
                }]},
                "result": {"log_dir": "old", "execution_summary": {"rerun_candidates": ["retryable"]}, "results": []},
            }), encoding="utf-8")
            retry = subprocess.run(
                ["bash", "localci", "retry", str(result_path), "--json", "--root", temporary],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
            retry_payload = json.loads(retry.stdout)
            self.assertEqual(retry_payload["route"]["selected"], "host")
            self.assertEqual(retry_payload["route"]["source"]["selected"], "host")

            graph = subprocess.run(
                ["bash", "localci", "graph", str(result_path), "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(graph.returncode, 0, graph.stdout + graph.stderr)
            self.assertEqual(json.loads(graph.stdout)["route"]["selected"], "host")

    def test_run_retry_graph_preserve_execution_history(self):
        if os.environ.get("LOCALCI_EXECUTOR_RUN") == "1":
            self.skipTest("avoid recursively invoking the history end-to-end test")
        with tempfile.TemporaryDirectory() as temporary:
            result_file = pathlib.Path(temporary) / "result.json"
            history_file = pathlib.Path(temporary) / "history.jsonl"
            run_result = subprocess.run(
                ["bash", "localci", "run", "--json", "--result-file", str(result_file), "--history-file", str(history_file), "--log-dir", str(pathlib.Path(temporary) / "logs")],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(run_result.returncode, 0, run_result.stdout + run_result.stderr)
            payload = json.loads(result_file.read_text(encoding="utf-8"))
            self.assertTrue(payload["execution_id"].startswith("run-"))
            self.assertEqual([item["event"] for item in payload["history"]], [
                "route_selected", "execution_started", "execution_completed",
            ])
            retry = subprocess.run(
                ["bash", "localci", "retry", str(result_file), "--json", "--history-file", str(history_file)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
            retry_payload = json.loads(retry.stdout)
            self.assertTrue(retry_payload["execution_id"].startswith("retry-"))
            self.assertEqual(retry_payload["history"][-2]["event"], "retry_started")
            self.assertEqual(retry_payload["history"][-1]["event"], "retry_completed")
            self.assertEqual(retry_payload["history"][-2]["source_execution_id"], payload["execution_id"])
            history = subprocess.run(
                ["bash", "localci", "history", "--history-file", str(history_file), "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(history.returncode, 0, history.stdout + history.stderr)
            records = json.loads(history.stdout)["records"]
            self.assertEqual([record["kind"] for record in records], ["run", "retry"])
            self.assertEqual(records[1]["source_execution_id"], payload["execution_id"])
            summary = subprocess.run(
                ["bash", "localci", "history", "--history-file", str(history_file), "--summary", "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(summary.returncode, 0, summary.stdout + summary.stderr)
            aggregate = json.loads(summary.stdout)["summary"]
            self.assertEqual(aggregate["records"], 2)
            self.assertIn("host", aggregate["by_backend"])
            self.assertGreaterEqual(aggregate["by_backend"]["host"]["runs"], 2)

    def test_history_filters_worktree_and_branch_and_summarizes_rechecks(self):
        with tempfile.TemporaryDirectory() as temporary:
            history_file = pathlib.Path(temporary) / "history.jsonl"
            records = [
                {
                    "execution_id": "retire-a", "kind": "worktree_retire",
                    "timestamp": "2026-09-30T12:00:00+00:00",
                    "backend": "unrouted", "status": "retired",
                    "worktree_retirement": {
                        "candidates": [{"branch": "feature/a", "path": "/tmp/formal-a"}],
                        "retired": [{"branch": "feature/a", "path": "/tmp/formal-a"}],
                        "recheck_results": [{"branch": "integration/a", "worktree": "/tmp/integration-a", "status": "success"}],
                    },
                },
                {
                    "execution_id": "retire-b", "kind": "worktree_retire",
                    "timestamp": "2026-10-01T12:00:00+00:00",
                    "backend": "unrouted", "status": "recheck_failed",
                    "worktree_retirement": {
                        "candidates": [{"branch": "feature/b", "path": "/tmp/formal-b"}],
                        "recheck_results": [{"branch": "integration/b", "worktree": "/tmp/integration-b", "status": "failed"}],
                    },
                },
            ]
            history_file.write_text("\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8")
            parents_file = pathlib.Path(temporary) / "branch-parents.json"
            parents_file.write_text(json.dumps({
                "integration/a": "main", "feature/a": "integration/a",
            }), encoding="utf-8")
            branch_result = subprocess.run(
                ["bash", "localci", "history", "--history-file", str(history_file),
                 "--kind", "worktree_retire", "--branch", "feature/a", "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(branch_result.returncode, 0, branch_result.stderr)
            self.assertEqual([item["execution_id"] for item in json.loads(branch_result.stdout)["records"]], ["retire-a"])

            worktree_result = subprocess.run(
                ["bash", "localci", "history", "--history-file", str(history_file),
                 "--worktree", "/tmp/formal-a", "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(worktree_result.returncode, 0, worktree_result.stderr)
            self.assertEqual(len(json.loads(worktree_result.stdout)["records"]), 1)

            summary_result = subprocess.run(
                ["bash", "localci", "history", "--history-file", str(history_file),
                 "--kind", "worktree_retire", "--summary", "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(summary_result.returncode, 0, summary_result.stderr)
            self.assertEqual(json.loads(summary_result.stdout)["summary"]["rechecks"], {
                "total": 2, "success": 1, "failed": 1, "blocked": 0, "success_rate": 0.5,
            })

            period_result = subprocess.run(
                ["bash", "localci", "history", "--history-file", str(history_file),
                 "--summary", "--json", "--since", "2026-09-30", "--until", "2026-09-30",
                 "--branch-parents-file", str(parents_file)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(period_result.returncode, 0, period_result.stderr)
            period_summary = json.loads(period_result.stdout)["summary"]
            self.assertEqual(period_summary["records"], 1)
            self.assertEqual(period_summary["branch_hierarchy"]["feature/a"]["generation"], 2)
            self.assertEqual(period_summary["by_worktree"]["/tmp/formal-a"]["retired"], 1)
            self.assertEqual(period_summary["rechecks"]["success_rate"], 1.0)

    def test_executor_prints_tail_excerpt_only_for_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            log_base = root / "logs"
            plan_path = root / "plan.json"
            command = "for i in $(seq 1 25); do echo line-$i; done; echo ERROR-final; exit 1"
            plan_path.write_text(json.dumps({
                "execution_allowed": True,
                "blocked": [],
                "backend": "host",
                "selected": [{"name": "failing-check", "command": command, "timeout_seconds": 30}],
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "scripts/local_executor.py", str(plan_path), "--root", str(root), "--log-dir", str(log_base), "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            item = payload["results"][0]
            self.assertIn("ERROR-final", item["error_excerpt"])
            self.assertIn("line-25", item["error_excerpt"])
            self.assertNotIn("line-1\n", item["error_excerpt"])

    def test_executor_prioritizes_error_highlights(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            plan_path = root / "plan.json"
            command = "printf 'warning: old\nfailed: check\nERROR: bad\nTraceback (most recent call last):\n'; exit 1"
            plan_path.write_text(json.dumps({
                "execution_allowed": True,
                "blocked": [],
                "backend": "host",
                "selected": [{"name": "classified-check", "command": command, "timeout_seconds": 30}],
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "scripts/local_executor.py", str(plan_path), "--root", str(root), "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            highlights = json.loads(result.stdout)["results"][0]["error_highlights"]
            self.assertEqual([item["category"] for item in highlights], ["traceback", "error", "failed", "warning"])

    def test_executor_builds_failure_summary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps({
                "execution_allowed": True,
                "blocked": [],
                "backend": "host",
                "selected": [{
                    "name": "build-check",
                    "stage": "build",
                    "command": "echo ERROR-build; exit 7",
                    "timeout_seconds": 30,
                }],
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "scripts/local_executor.py", str(plan_path), "--root", str(root), "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            summary = json.loads(result.stdout)["failure_summary"]
            self.assertEqual(summary["stage"], "build")
            self.assertEqual(summary["command"], "build-check")
            self.assertEqual(summary["returncode"], 7)
            self.assertIn("error", summary["categories"])

    def test_executor_reports_commands_not_run_after_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            marker = root / "should-not-start"
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps({
                "execution_allowed": True,
                "blocked": [],
                "backend": "host",
                "selected": [
                    {"name": "test-check", "stage": "test", "command": "exit 3", "timeout_seconds": 30},
                    {"name": "build-check", "stage": "build", "command": f"touch '{marker}'", "timeout_seconds": 30},
                ],
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "scripts/local_executor.py", str(plan_path), "--root", str(root), "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            summary = payload["execution_summary"]
            self.assertEqual(summary["total"], 2)
            self.assertEqual(summary["completed"], 1)
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(summary["not_run"], 1)
            self.assertEqual(payload["results"][1]["status"], "not_run")
            self.assertFalse(marker.exists())

    def test_executor_classifies_not_run_reason_and_rerun_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps({
                "execution_allowed": True,
                "blocked": [],
                "backend": "host",
                "selected": [
                    {"name": "install", "stage": "install", "command": "exit 2", "timeout_seconds": 30},
                    {"name": "test", "stage": "test", "depends_on": ["install"], "command": "exit 0", "timeout_seconds": 30},
                    {"name": "docs", "stage": "build", "command": "exit 0", "timeout_seconds": 30},
                    {"name": "release", "stage": "build", "depends_on": ["test"], "retryable": False, "command": "exit 0", "timeout_seconds": 30},
                ],
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "scripts/local_executor.py", str(plan_path), "--root", str(root), "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            results = {item["name"]: item for item in payload["results"]}
            self.assertEqual(results["test"]["reason"], "dependency_failed")
            self.assertEqual(results["test"]["blocked_by"], ["install"])
            self.assertFalse(results["test"]["rerunnable"])
            self.assertEqual(results["docs"]["reason"], "policy_stop_after_failure")
            self.assertTrue(results["docs"]["rerunnable"])
            self.assertEqual(results["release"]["reason"], "dependency_not_run")
            self.assertFalse(results["release"]["rerunnable"])
            self.assertEqual(payload["execution_summary"]["rerun_candidates"], ["install", "docs"])

    def test_manifest_accepts_dependency_and_retry_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = pathlib.Path(temporary) / "manifest.json"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "commands": [{
                    "name": "install", "stage": "install", "command": "echo install",
                    "requirements": {"os": ["macos"], "docker": False, "services": [], "tools": []},
                    "profiles": ["standard"], "timeout_seconds": 30,
                }, {
                    "name": "check",
                    "stage": "test",
                    "command": "echo ok",
                    "requirements": {"os": ["macos"], "docker": False, "services": [], "tools": []},
                    "profiles": ["standard"], "timeout_seconds": 30,
                    "depends_on": ["install"], "retryable": False,
                }],
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "scripts/validate_command_manifest.py", str(manifest)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_executor_restores_cache_and_invalidates_corruption(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            cache_dir = root / "cache"
            marker = root / "runs"
            output = root / "dist.txt"
            plan = root / "plan.json"
            item = {
                "name": "cached-build", "stage": "build",
                "command": f"python3 -c \"open('{marker}', 'a').write('x'); open('{output}', 'w').write('ok')\"",
                "cache": {"paths": ["dist.txt"], "key_files": []}, "timeout_seconds": 30,
            }
            plan.write_text(json.dumps({"execution_allowed": True, "blocked": [], "backend": "host", "selected": [item]}), encoding="utf-8")
            env = {**os.environ, "LOCALCI_CACHE_DIR": str(cache_dir)}
            first = subprocess.run([sys.executable, "scripts/local_executor.py", str(plan), "--root", str(root), "--json"],
                                   cwd=ROOT, env=env, text=True, capture_output=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(json.loads(first.stdout)["results"][0]["status"], "success")
            second = subprocess.run([sys.executable, "scripts/local_executor.py", str(plan), "--root", str(root), "--json"],
                                    cwd=ROOT, env=env, text=True, capture_output=True)
            self.assertEqual(json.loads(second.stdout)["results"][0]["status"], "cached")
            archive = next(cache_dir.glob("*.tar.gz"))
            archive.write_bytes(b"broken")
            third = subprocess.run([sys.executable, "scripts/local_executor.py", str(plan), "--root", str(root), "--json"],
                                   cwd=ROOT, env=env, text=True, capture_output=True)
            payload = json.loads(third.stdout)
            self.assertEqual(payload["results"][0]["status"], "success")
            self.assertEqual(payload["results"][0]["cache"]["status"], "corrupt")
            self.assertEqual(marker.read_text(encoding="utf-8"), "xx")

    def test_manifest_rejects_unknown_dependency_and_stage_cycle(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = pathlib.Path(temporary) / "manifest.json"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "commands": [
                    {"name": "prepare", "stage": "install", "command": "echo prepare",
                     "requirements": {"os": ["linux"], "docker": False, "services": [], "tools": []},
                     "profiles": ["standard"], "timeout_seconds": 30, "depends_on": ["missing"]},
                    {"name": "test", "stage": "test", "command": "echo test",
                     "requirements": {"os": ["linux"], "docker": False, "services": [], "tools": []},
                     "profiles": ["standard"], "timeout_seconds": 30, "depends_on": ["build"]},
                    {"name": "build", "stage": "build", "command": "echo build",
                     "requirements": {"os": ["linux"], "docker": False, "services": [], "tools": []},
                     "profiles": ["standard"], "timeout_seconds": 30, "depends_on": ["test"]},
                ],
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "scripts/validate_command_manifest.py", str(manifest)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("unknown command: missing", result.stderr)
            self.assertIn("dependency cycle across stages", result.stderr)

    def test_manifest_rejects_os_tool_contradiction_and_unset_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = pathlib.Path(temporary) / "manifest.json"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "commands": [{
                    "name": "invalid", "stage": "test", "command": " ",
                    "requirements": {"os": ["linux"], "docker": False, "services": [], "tools": ["powershell"]},
                    "profiles": ["standard"], "timeout_seconds": 30,
                }],
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "scripts/validate_command_manifest.py", str(manifest)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("command is required", result.stderr)
            self.assertIn("contradict requirements.os", result.stderr)

    def test_plan_blocks_missing_command_before_executor(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = pathlib.Path(temporary) / "manifest.json"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "commands": [{
                    "name": "missing-command", "stage": "test", "command": "localci-command-does-not-exist --check",
                    "requirements": {"os": ["linux", "macos", "windows"], "docker": False, "services": [], "tools": []},
                    "always": True, "profiles": ["standard"], "timeout_seconds": 30,
                }],
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "scripts/localci_plan.py", "--json", "--inventory", str(manifest)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout)
            self.assertFalse(plan["execution_allowed"])
            self.assertIn("command=localci-command-does-not-exist", plan["blocked"][0]["missing"])

    def test_retry_runs_only_rerun_candidates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            marker = root / "retried"
            result_path = root / "result.json"
            result_path.write_text(json.dumps({
                "plan": {
                    "backend": "host",
                    "execution_allowed": True,
                    "blocked": [],
                    "selected": [
                        {"name": "failed-check", "stage": "test", "command": "exit 2", "timeout_seconds": 30},
                        {"name": "independent-check", "stage": "build", "command": f"touch '{marker}'", "timeout_seconds": 30},
                        {"name": "successful-check", "stage": "test", "command": "exit 0", "timeout_seconds": 30},
                    ],
                },
                "result": {"log_dir": "old-run", "execution_summary": {"rerun_candidates": ["independent-check"]}},
            }), encoding="utf-8")
            result = subprocess.run(
                ["bash", "localci", "retry", str(result_path), "--root", str(root), "--log-dir", str(root / "logs"), "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual([item["name"] for item in output["retry_plan"]["selected"]], ["independent-check"])
            self.assertEqual(output["result"]["status"], "success")
            self.assertTrue(marker.exists())

    def test_retry_without_candidates_does_not_execute(self):
        with tempfile.TemporaryDirectory() as temporary:
            result_path = pathlib.Path(temporary) / "result.json"
            result_path.write_text(json.dumps({
                "plan": {"backend": "host", "execution_allowed": True, "blocked": [], "selected": []},
                "result": {"execution_summary": {"rerun_candidates": []}},
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "scripts/localci_retry.py", str(result_path), "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["result"]["status"], "no_candidates")

    def test_retry_orders_candidates_by_dependencies(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            order_file = root / "order"
            result_path = root / "result.json"
            result_path.write_text(json.dumps({
                "plan": {
                    "backend": "host", "execution_allowed": True, "blocked": [],
                    "selected": [
                        {"name": "build", "stage": "build", "depends_on": ["test"],
                         "command": f"echo build >> '{order_file}'", "timeout_seconds": 30},
                        {"name": "test", "stage": "test", "depends_on": ["install"],
                         "command": f"echo test >> '{order_file}'", "timeout_seconds": 30},
                        {"name": "install", "stage": "install",
                         "command": f"echo install >> '{order_file}'", "timeout_seconds": 30},
                    ],
                },
                "result": {"execution_summary": {"rerun_candidates": ["build", "test", "install"]}},
            }), encoding="utf-8")
            result = subprocess.run(
                ["bash", "localci", "retry", str(result_path), "--root", str(root), "--log-dir", str(root / "logs"), "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual(output["retry_plan"]["retry_order"], ["install", "test", "build"])
            self.assertEqual(order_file.read_text(encoding="utf-8").splitlines(), ["install", "test", "build"])

    def test_retry_rejects_candidate_dependency_cycle(self):
        with tempfile.TemporaryDirectory() as temporary:
            result_path = pathlib.Path(temporary) / "result.json"
            result_path.write_text(json.dumps({
                "plan": {"backend": "host", "execution_allowed": True, "blocked": [], "selected": [
                    {"name": "a", "depends_on": ["b"], "command": "exit 0"},
                    {"name": "b", "depends_on": ["a"], "command": "exit 0"},
                ]},
                "result": {"execution_summary": {"rerun_candidates": ["a", "b"]}},
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "scripts/localci_retry.py", str(result_path)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("dependency cycle", result.stderr)

    def test_graph_shows_dependencies_and_retry_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            result_path = pathlib.Path(temporary) / "result.json"
            result_path.write_text(json.dumps({
                "plan": {"selected": [
                    {"name": "build", "stage": "build", "depends_on": ["test"]},
                    {"name": "test", "stage": "test", "depends_on": ["install"]},
                    {"name": "install", "stage": "install"},
                ]},
                "result": {
                    "execution_summary": {"rerun_candidates": ["build", "test", "install"]},
                    "results": [{"name": "build", "status": "not_run"}, {"name": "test", "status": "failed"}, {"name": "install", "status": "failed"}],
                },
            }), encoding="utf-8")
            result = subprocess.run(
                ["bash", "localci", "graph", str(result_path), "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            graph = json.loads(result.stdout)
            self.assertEqual(graph["retry_order"], ["install", "test", "build"])
            install_edge = next(edge for edge in graph["edges"] if edge["from"] == "install" and edge["to"] == "test")
            build_edge = next(edge for edge in graph["edges"] if edge["from"] == "test" and edge["to"] == "build")
            self.assertEqual(install_edge["from_stage"], "install")
            self.assertEqual(install_edge["to_stage"], "test")
            self.assertEqual(build_edge["label"], "#3 / time unknown")

    def test_graph_rejects_dependency_cycle(self):
        with tempfile.TemporaryDirectory() as temporary:
            result_path = pathlib.Path(temporary) / "result.json"
            result_path.write_text(json.dumps({
                "plan": {"selected": [
                    {"name": "a", "depends_on": ["b"]},
                    {"name": "b", "depends_on": ["a"]},
                ]},
                "result": {"execution_summary": {"rerun_candidates": ["a", "b"]}},
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "scripts/localci_graph.py", str(result_path)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("dependency cycle", result.stderr)

    def test_graph_outputs_mermaid_flowchart(self):
        with tempfile.TemporaryDirectory() as temporary:
            result_path = pathlib.Path(temporary) / "result.json"
            result_path.write_text(json.dumps({
                "plan": {"selected": [
                    {"name": "build", "stage": "build", "depends_on": ["test"]},
                    {"name": "test", "stage": "test"},
                ]},
                "result": {"execution_summary": {"rerun_candidates": ["build"]}},
            }), encoding="utf-8")
            result = subprocess.run(
                ["bash", "localci", "graph", str(result_path), "--mermaid"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("flowchart TD", result.stdout)
            self.assertIn("-->", result.stdout)
            self.assertIn("classDef status_success", result.stdout)
            self.assertIn("classDef status_failed", result.stdout)
            self.assertIn("classDef status_not_run", result.stdout)

    def test_graph_mermaid_uses_status_colors(self):
        with tempfile.TemporaryDirectory() as temporary:
            result_path = pathlib.Path(temporary) / "result.json"
            result_path.write_text(json.dumps({
                "plan": {"selected": [
                    {"name": "ok", "stage": "test"},
                    {"name": "bad", "stage": "test"},
                    {"name": "skipped", "stage": "build"},
                ]},
                "result": {
                    "execution_summary": {"rerun_candidates": ["bad", "skipped"]},
                    "results": [
                        {"name": "ok", "status": "success"},
                        {"name": "bad", "status": "failed"},
                        {"name": "skipped", "status": "not_run"},
                    ],
                },
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "scripts/localci_graph.py", str(result_path), "--mermaid"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("n0[\"ok\\ntest / success\"]:::status_success", result.stdout)
            self.assertIn("status_failed_rerun", result.stdout)
            self.assertIn("status_not_run_rerun", result.stdout)

    def test_graph_mermaid_groups_nodes_by_stage(self):
        with tempfile.TemporaryDirectory() as temporary:
            result_path = pathlib.Path(temporary) / "result.json"
            result_path.write_text(json.dumps({
                "plan": {"selected": [
                    {"name": "install", "stage": "install"},
                    {"name": "test", "stage": "test", "depends_on": ["install"]},
                    {"name": "typecheck", "stage": "typecheck", "depends_on": ["test"]},
                    {"name": "build", "stage": "build", "depends_on": ["typecheck"]},
                ]},
                "result": {"execution_summary": {"rerun_candidates": []}, "results": [
                    {"name": "install", "status": "success", "duration_seconds": 1.25},
                    {"name": "test", "status": "failed", "duration_seconds": 2.5},
                    {"name": "typecheck", "status": "not_run"},
                    {"name": "build", "status": "success", "duration_seconds": 3.0},
                ]},
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "scripts/localci_graph.py", str(result_path), "--mermaid"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('["install (time=1.250s, success=1, failed=0, blocked=0, timeout=0, not_run=0)"]', result.stdout)
            self.assertIn('["test (time=2.500s, success=0, failed=1, blocked=0, timeout=0, not_run=0)"]', result.stdout)
            self.assertIn('["typecheck (time=unknown, success=0, failed=0, blocked=0, timeout=0, not_run=1)"]', result.stdout)
            self.assertIn('["build (time=3.000s, success=1, failed=0, blocked=0, timeout=0, not_run=0, BOTTLENECK]"]', result.stdout)
            self.assertGreaterEqual(result.stdout.count("subgraph stage_"), 4)

    def test_graph_identifies_bottleneck_stage(self):
        with tempfile.TemporaryDirectory() as temporary:
            result_path = pathlib.Path(temporary) / "result.json"
            result_path.write_text(json.dumps({
                "plan": {"selected": [
                    {"name": "install", "stage": "install"},
                    {"name": "build", "stage": "build"},
                ]},
                "result": {"execution_summary": {"rerun_candidates": []}, "results": [
                    {"name": "install", "status": "success", "duration_seconds": 1.0},
                    {"name": "build", "status": "success", "duration_seconds": 4.5},
                ]},
            }), encoding="utf-8")
            result = subprocess.run(
                ["bash", "localci", "graph", str(result_path), "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            graph = json.loads(result.stdout)
            self.assertEqual(graph["bottleneck_stage"], {"name": "build", "duration_seconds": 4.5})
            self.assertEqual(graph["parallel_candidates"], ["build"])
            mermaid = subprocess.run(
                ["bash", "localci", "graph", str(result_path), "--mermaid"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(mermaid.returncode, 0, mermaid.stdout + mermaid.stderr)
            self.assertIn("BOTTLENECK", mermaid.stdout)

    def test_graph_finds_parallel_candidates_in_bottleneck_stage(self):
        with tempfile.TemporaryDirectory() as temporary:
            result_path = pathlib.Path(temporary) / "result.json"
            result_path.write_text(json.dumps({
                "plan": {"selected": [
                    {"name": "a", "stage": "build"},
                    {"name": "b", "stage": "build"},
                    {"name": "c", "stage": "build", "depends_on": ["a"]},
                ]},
                "result": {"execution_summary": {"rerun_candidates": []}, "results": [
                    {"name": "a", "status": "success", "duration_seconds": 2.0},
                    {"name": "b", "status": "success", "duration_seconds": 3.0},
                    {"name": "c", "status": "success", "duration_seconds": 1.0},
                ]},
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "scripts/localci_graph.py", str(result_path), "--json"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)["parallel_candidates"], ["a", "b"])

    def test_graph_mermaid_labels_dependency_edges(self):
        with tempfile.TemporaryDirectory() as temporary:
            result_path = pathlib.Path(temporary) / "result.json"
            result_path.write_text(json.dumps({
                "plan": {"selected": [
                    {"name": "install", "stage": "install"},
                    {"name": "test", "stage": "test", "depends_on": ["install"]},
                ]},
                "result": {"execution_summary": {"rerun_candidates": ["install", "test"]}, "results": [
                    {"name": "install", "status": "success", "duration_seconds": 1.0},
                    {"name": "test", "status": "failed", "duration_seconds": 2.25},
                ]},
            }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "scripts/localci_graph.py", str(result_path), "--mermaid"],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("-->|#2 / 2.250s|", result.stdout)

    def test_retry_failed_only_filters_out_not_run_candidates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            failed_marker = root / "failed-retry"
            not_run_marker = root / "not-run-retry"
            result_path = root / "result.json"
            result_path.write_text(json.dumps({
                "plan": {
                    "backend": "host", "execution_allowed": True, "blocked": [],
                    "selected": [
                        {"name": "failed-check", "stage": "test", "command": f"touch '{failed_marker}'", "timeout_seconds": 30},
                        {"name": "not-run-check", "stage": "test", "command": f"touch '{not_run_marker}'", "timeout_seconds": 30},
                    ],
                },
                "result": {
                    "execution_summary": {"rerun_candidates": ["failed-check", "not-run-check"]},
                    "results": [
                        {"name": "failed-check", "stage": "test", "status": "failed"},
                        {"name": "not-run-check", "stage": "test", "status": "not_run"},
                    ],
                },
            }), encoding="utf-8")
            result = subprocess.run(
                ["bash", "localci", "retry", str(result_path), "--failed-only", "--root", str(root)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(failed_marker.exists())
            self.assertFalse(not_run_marker.exists())

    def test_retry_stage_filters_candidates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            test_marker = root / "test-retry"
            build_marker = root / "build-retry"
            result_path = root / "result.json"
            result_path.write_text(json.dumps({
                "plan": {
                    "backend": "host", "execution_allowed": True, "blocked": [],
                    "selected": [
                        {"name": "test-check", "stage": "test", "command": f"touch '{test_marker}'", "timeout_seconds": 30},
                        {"name": "build-check", "stage": "build", "command": f"touch '{build_marker}'", "timeout_seconds": 30},
                    ],
                },
                "result": {"execution_summary": {"rerun_candidates": ["test-check", "build-check"]}},
            }), encoding="utf-8")
            result = subprocess.run(
                ["bash", "localci", "retry", str(result_path), "--stage", "build", "--root", str(root)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(test_marker.exists())
            self.assertTrue(build_marker.exists())
    def test_change_discovery_reports_worktree_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            (root / "tracked.txt").write_text("before\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)
            (root / "tracked.txt").write_text("after\n", encoding="utf-8")
            (root / "new.txt").write_text("new\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/discover_changes.py"),
                 "--json", "--root", str(root)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["count"], 2)
            self.assertEqual({item["path"] for item in payload["changed_files"]}, {"new.txt", "tracked.txt"})

    def test_command_manifest_example_is_valid(self):
        result = subprocess.run(
            [sys.executable, "scripts/validate_command_manifest.py",
             ".localci/product-commands.example.json"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_policy_verifier_passes(self):
        result = subprocess.run(
            [sys.executable, "scripts/policy_verify.py"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("product checks: delegated to localci run", result.stdout)

    def test_quiet_runner_passes_once(self):
        result = subprocess.run(
            ["bash", "scripts/run_ci_local_quiet.sh"],
            cwd=ROOT, text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("policy_verify.py", result.stdout)


if __name__ == "__main__":
    unittest.main()
