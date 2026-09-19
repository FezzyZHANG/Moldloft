"""Offline migration/bootstrap regressions; no real agent or user configuration is used."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/agent_hooks"))
import bootstrap
import startup_sanity as sanity
import sanity_audit as audit


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="moldloft-bootstrap-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "copy with 'quotes' $dollars and unicode-测试"
        self.root.mkdir()
        shutil.copyfile(ROOT / "AGENTS.md", self.root / "AGENTS.md")
        (self.root / "docs").mkdir()
        shutil.copyfile(ROOT / "docs/agent-principles.md", self.root / "docs/agent-principles.md")
        shutil.copytree(ROOT / "tools/agent_hooks", self.root / "tools/agent_hooks",
                        ignore=shutil.ignore_patterns("__pycache__"))

    def config(self, host, value):
        path = self.root / bootstrap.HOST_FILES[host]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def test_portable_copy_needs_only_its_own_contract_and_standard_library(self):
        result = subprocess.run([sys.executable, "-B", "-S", str(self.root / "tools/agent_hooks/bootstrap.py"),
                                 "--check", "--host", "codex"], cwd=self.temp.name,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 1)
        data = json.loads(result.stdout)
        self.assertEqual(data["configurations"][0]["status"], "missing")
        self.assertEqual(data["question_anchors"], len(sanity.QUESTIONS))
        self.assertFalse((self.root / ".codex").exists())

    def test_anchor_drift_is_reported_before_installation(self):
        (self.root / "AGENTS.md").write_text("No known rules", encoding="utf-8")
        with self.assertRaisesRegex(bootstrap.BootstrapError, "anchors"):
            bootstrap.validate_contract(self.root)
        self.assertFalse((self.root / ".codex").exists())

    def test_wrapping_changes_do_not_expire_correct_rule_anchors(self):
        text = sanity.contract_text(self.root / "AGENTS.md")
        self.assertEqual(len(sanity._valid_questions("\n".join(text.split()))), len(sanity.QUESTIONS))

    def test_missing_install_and_idempotent_reinstall_preserve_exact_bytes(self):
        plans = [bootstrap.plan(host, self.root) for host in bootstrap.HOST_FILES]
        self.assertTrue(all(item["status"] == "missing" for item in plans))
        bootstrap.apply_plans(plans)
        before = {item["path"]: (item["path"].read_bytes(), item["path"].stat().st_mtime_ns) for item in plans}
        repeated = [bootstrap.plan(host, self.root) for host in bootstrap.HOST_FILES]
        self.assertTrue(all(item["status"] == "ready" for item in repeated))
        bootstrap.apply_plans(repeated)
        for path, original in before.items():
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), original)

    def test_unrelated_settings_and_handlers_survive_stale_relocation(self):
        foreign = {"type": "command", "command": "fixture-only-foreign-check"}
        old = bootstrap.definitions("claude", Path(self.temp.name) / "old-copy")
        current = {"permissions": {"allow": ["Read"]}, "privateFixture": "preserve",
                   "hooks": {**deepcopy(old), "PreToolUse": [{"matcher": "Read", "hooks": [foreign]}]}}
        current["hooks"]["Stop"][0]["hooks"].append(foreign)
        path = self.config("claude", current)
        receipt = self.root / ".moldloft/hooks.local.json"
        receipt.parent.mkdir()
        receipt.write_text(json.dumps({"claude": {"development": old}}), encoding="utf-8")
        item = bootstrap.plan("claude", self.root)
        self.assertEqual(item["status"], "stale")
        bootstrap.apply_plans([item])
        updated = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(updated["permissions"], current["permissions"])
        self.assertEqual(updated["privateFixture"], "preserve")
        self.assertEqual(updated["hooks"]["PreToolUse"], current["hooks"]["PreToolUse"])
        self.assertEqual(updated["hooks"]["Stop"][0]["hooks"], [foreign])
        self.assertEqual(bootstrap.plan("claude", self.root)["status"], "ready")

    def test_wrong_json_and_disabled_settings_are_not_overwritten(self):
        for value in ([], {"hooks": []}, {"hooks": {"enabled": False}}, {"disableAllHooks": True}):
            with self.subTest(value=value):
                path = self.config("zcode", value)
                before = path.read_bytes()
                with self.assertRaises(bootstrap.BootstrapError):
                    bootstrap.plan("zcode", self.root)
                self.assertEqual(path.read_bytes(), before)
        path.write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(bootstrap.BootstrapError, "invalid local JSON"):
            bootstrap.plan("zcode", self.root)
        self.assertEqual(path.read_text(encoding="utf-8"), "{broken")

    def test_explicitly_disabled_owned_handler_is_preserved(self):
        current = {"hooks": bootstrap.definitions("codex", self.root)}
        current["hooks"]["Stop"][0]["hooks"][0]["enabled"] = False
        path = self.config("codex", current)
        before = path.read_bytes()
        with self.assertRaisesRegex(bootstrap.BootstrapError, "disabled"):
            bootstrap.plan("codex", self.root)
        self.assertEqual(path.read_bytes(), before)

    def test_concurrent_edit_aborts_before_any_config_write(self):
        plans = [bootstrap.plan(host, self.root) for host in ("codex", "claude")]
        self.config("claude", {"permissionFixture": "new edit"})
        with self.assertRaisesRegex(bootstrap.BootstrapError, "changed during"):
            bootstrap.apply_plans(plans)
        self.assertFalse((self.root / ".codex/hooks.json").exists())

    def test_merge_is_pure_and_removes_only_duplicate_owned_handlers(self):
        expected = bootstrap.definitions("codex", self.root)
        current = {"hooks": deepcopy(expected)}
        current["hooks"]["Stop"] *= 2
        original = deepcopy(current)
        merged = bootstrap.merge_config(current, expected, "codex")
        self.assertEqual(current, original)
        self.assertEqual(merged, {"hooks": expected})

    def test_all_generated_hosts_have_only_their_supported_events(self):
        for host in bootstrap.HOST_FILES:
            with self.subTest(host=host):
                events = bootstrap.definitions(host, self.root)
                self.assertEqual(set(events), {"SessionStart", "Stop"} if host == "zcode" else set(bootstrap.EVENTS))
                for groups in events.values():
                    self.assertEqual(len(groups), 1)
                    command = groups[0]["hooks"][0]
                    self.assertTrue(bootstrap._managed(command))
                    self.assertNotIn("dangerously", json.dumps(command))
                    if host != "codex":
                        self.assertEqual(command["args"][-2:], ["--host", host])

    def test_generated_launchers_handle_spaces_quotes_unicode_and_unrelated_cwd(self):
        for host in bootstrap.HOST_FILES:
            with self.subTest(host=host):
                result = bootstrap.self_test(host, self.root)
                self.assertEqual(result["synthetic_lifecycle"], "passed")
                self.assertEqual(result["live_host_activation"], "not_verified")
                self.assertFalse((self.root / bootstrap.HOST_FILES[host]).exists())

    def test_install_cli_from_unrelated_directory_is_repeatable(self):
        script = self.root / "tools/agent_hooks/bootstrap.py"

        def run(mode):
            result = subprocess.run([sys.executable, "-B", "-S", str(script), mode, "--host", "claude"],
                                    cwd=self.temp.name, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout)
            data = json.loads(result.stdout)
            self.assertEqual(data["configurations"][0]["status"], "ready")
            self.assertEqual(data["live_host_activation"], "not_verified")

        run("--install")
        path = self.root / bootstrap.HOST_FILES["claude"]
        before = path.read_bytes()
        run("--check")
        run("--install")
        self.assertEqual(path.read_bytes(), before)

    def test_each_checkout_and_host_has_its_own_opaque_default_state(self):
        paths = {audit.state_directory(root, host=host, temporary_root=self.temp.name)
                 for root in (self.root, Path(self.temp.name) / "second") for host in audit.HOSTS}
        self.assertEqual(len(paths), 6)
        self.assertTrue(all("$dollars" not in str(path) for path in paths))

    def test_expired_pending_file_is_not_graded(self):
        state_dir = Path(self.temp.name) / "fixture-state"
        payload = {"hook_event_name": "SessionStart", "session_id": "fixture"}
        sanity.handle_hook(payload, state_dir=state_dir, agents_path=self.root / "AGENTS.md")
        path = sanity.state_path(payload, "Stop", state_dir)
        value = json.loads(path.read_text(encoding="utf-8"))
        value["issued_at"] = 0
        path.write_text(json.dumps(value), encoding="utf-8")
        result = sanity.handle_hook({"hook_event_name": "Stop", "session_id": "fixture"}, state_dir=state_dir)
        self.assertIsNone(result)
        self.assertFalse(path.exists())

    def test_access_prunes_only_expired_opaque_pending_names(self):
        state_dir = Path(self.temp.name) / "fixture-state"
        state_dir.mkdir()
        owned = state_dir / ("a" * 64 + ".json")
        other = state_dir / "operator.json"
        for path in (owned, other):
            path.write_text("do not read this fixture", encoding="utf-8")
            os.utime(path, (0, 0))
        sanity.main(io.StringIO('{"hook_event_name":"Stop","session_id":"fixture"}'),
                    io.StringIO(), state_dir=state_dir)
        self.assertFalse(owned.exists())
        self.assertTrue(other.exists())

    def test_relative_state_override_fails_open_without_creating_files(self):
        output = io.StringIO()
        self.assertEqual(sanity.main(io.StringIO("{}"), output, state_dir="relative-fixture"), 0)
        self.assertIn("must be absolute", json.loads(output.getvalue())["systemMessage"])


if __name__ == "__main__":
    unittest.main()
