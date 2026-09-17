"""Standalone boundaries, using temporary state and synthetic provider responses."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from fastapi.testclient import TestClient

from tickets import config, copilot, llm
from tickets.app import create_app
from tickets.gitinfo import GitInfo
from tickets.translate import TranslationError, translate_ticket


class StandaloneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith(("TICKETS_", "KIMI_", "ZAI_", "GLM_"))}
        self.env = patch.dict(os.environ, self.environment, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_defaults_are_local_empty_and_disabled(self):
        self.assertEqual(config.repository_roots(), {})
        self.assertFalse(config.enabled("TICKETS_COPILOT"))
        self.assertFalse(config.enabled("TICKETS_TRANSLATION"))
        database, notebook = config.data_paths()
        self.assertTrue(database.is_absolute())
        self.assertEqual(notebook, database.parent / "notes")
        self.assertNotIn("site-packages", database.parts)

    def test_only_explicit_env_file_is_read_and_process_wins(self):
        secret_file = self.root / "models.env"
        secret_file.write_text('TICKETS_COPILOT=on\nZAI_API_KEY="synthetic-file-key"\n', encoding="utf-8")
        with patch.dict(os.environ, {"TICKETS_ENV_FILE": str(secret_file), "ZAI_API_KEY": "synthetic-process-key"}):
            self.assertTrue(config.enabled("TICKETS_COPILOT"))
            self.assertEqual(os.environ["ZAI_API_KEY"], "synthetic-process-key")
        with patch.dict(os.environ, {"TICKETS_ENV_FILE": "relative.env"}):
            with self.assertRaises(config.ConfigurationError):
                config.load_env_file()

    def test_absolute_paths_and_factory_precedence(self):
        with patch.dict(os.environ, {"TICKETS_DB": str(self.root / "environment.db")}):
            self.assertEqual(config.data_paths()[0], self.root / "environment.db")
            db, notes = config.data_paths(self.root / "fixture.db", self.root / "notebook")
            self.assertEqual((db, notes), (self.root / "fixture.db", self.root / "notebook"))
        for arguments in (("relative.db",), (self.root / "fixture.db", "relative-notes")):
            with self.subTest(arguments=arguments), self.assertRaises(config.ConfigurationError):
                config.data_paths(*arguments)

    def test_repository_map_controls_backend_ui_and_model_schema(self):
        registry = self.root / "repositories.json"
        registry.write_text(json.dumps({"project-alpha": str(self.root)}), encoding="utf-8")
        with patch.dict(os.environ, {"TICKETS_REPOSITORIES": str(registry)}):
            self.assertEqual(GitInfo().roots, {"project-alpha": self.root})
            app = create_app(self.root / "tickets.db")
            with TestClient(app) as client:
                self.assertEqual(client.get("/api/git/repos").json(), {"repos": ["project-alpha"]})
                self.assertIn('<option value="project-alpha">project-alpha</option>', client.get("/graph").text)
                tool = copilot.tool_for(app.state.store)
                repo = tool["function"]["parameters"]["properties"]["operations"]["items"]["properties"]["link"]["properties"]["repo"]
                self.assertEqual(repo["enum"], ["project-alpha"])
        registry.write_text('{"project": "relative/root"}', encoding="utf-8")
        with patch.dict(os.environ, {"TICKETS_REPOSITORIES": str(registry)}):
            with self.assertRaises(config.ConfigurationError):
                config.repository_roots()

    def test_offline_empty_app_assets_writes_and_restart(self):
        database = self.root / "tickets.db"
        with patch.object(llm.urllib.request, "urlopen", side_effect=AssertionError("Unexpected model call")):
            app = create_app(database)
            with TestClient(app) as client:
                graph = client.get("/api/graph").json()
                self.assertEqual(graph["tickets"], [])
                self.assertEqual(graph["notes"], [])
                self.assertFalse(app.state.translation_enabled)
                self.assertIn("<small>未启用</small>", client.get("/graph").text)
                for route in ("/", "/graph", "/static/style.css", "/static/graph.css", "/static/graph.js",
                              "/static/layouts/compact-layered.js", "/static/vendor/elk/elk-api.js",
                              "/static/vendor/elk/elk-worker.min.js"):
                    with self.subTest(route=route):
                        self.assertEqual(client.get(route).status_code, 200)
                response = client.post("/api/tickets", json={"fields": {"title": "Isolated work"},
                    "revision": graph["revision"], "request_id": "standalone-create"})
                self.assertEqual(response.status_code, 200)
                note = client.put("/api/notes/n-fixture", json={"fields": {"title": "A premise",
                    "body": "Synthetic evidence only."}, "version": None})
                self.assertEqual(note.status_code, 200)
                chat = client.post("/api/copilot/chat", json={"messages": [{"role": "user", "content": "Synthetic"}]})
                self.assertIn("Model feature is disabled", chat.text)
                self.assertNotIn('"type": "proposal"', chat.text)
                self.assertEqual(client.get("/api/graph").json()["tickets"][0]["title"], "Isolated work")
            with TestClient(create_app(database)) as client:
                graph = client.get("/api/graph").json()
                self.assertEqual(len(graph["tickets"]), 1)
                self.assertEqual(graph["notes"][0]["body"], "Synthetic evidence only.")

    def test_import_from_unrelated_directory_has_no_state_or_host_access(self):
        host = self.root / "os"
        host.mkdir()
        (host / ".env").write_text("ZAI_API_KEY=must-not-load\n", encoding="utf-8")
        selected_db = self.root / "unopened" / "tickets.db"
        environment = dict(os.environ, TICKETS_DB=str(selected_db))
        code = "import os; import tickets.app, tickets.translate; assert 'ZAI_API_KEY' not in os.environ; print('isolated-import-ok')"
        result = subprocess.run([sys.executable, "-I", "-B", "-c", code], cwd=self.root,
                                env=environment, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("isolated-import-ok", result.stdout)
        self.assertFalse(selected_db.parent.exists())

    def test_keys_do_not_enable_paid_features(self):
        with patch.dict(os.environ, {"ZAI_API_KEY": "fixture", "KIMI_API_KEY": "fixture"}), \
                patch.object(llm.urllib.request, "urlopen") as opening:
            with self.assertRaisesRegex(llm.LLMError, "disabled"):
                list(llm.stream("tickets_copilot", messages=[], system="Fixture"))
            with self.assertRaisesRegex(TranslationError, "disabled"):
                translate_ticket("Fixture", "Body")
            opening.assert_not_called()

    def test_translation_payload_and_sanitized_failures(self):
        captured = []

        def opening(request, timeout):
            captured.append(json.loads(request.data))
            return io.BytesIO(json.dumps({"choices": [{"finish_reason": "stop", "message": {
                "content": "TITLE: 示例\nDESCRIPTION: 测试"}}]}).encode("utf-8"))

        with patch.dict(os.environ, {"TICKETS_TRANSLATION": "on", "KIMI_API_KEY": "fixture"}), \
                patch.object(llm.urllib.request, "urlopen", side_effect=opening):
            self.assertEqual(translate_ticket("Example", "Synthetic"), ("示例", "测试"))
        self.assertEqual(captured[0]["temperature"], 1.0)
        self.assertIn("Synthetic", captured[0]["messages"][-1]["content"])
        failure = urllib.error.HTTPError("https://example.invalid", 401, "denied", {}, io.BytesIO(b"private-body"))
        with patch.dict(os.environ, {"TICKETS_TRANSLATION": "on", "KIMI_API_KEY": "fixture"}), \
                patch.object(llm.urllib.request, "urlopen", side_effect=failure):
            with self.assertRaisesRegex(TranslationError, "HTTP 401") as result:
                translate_ticket("Fixture", "Body")
        self.assertNotIn("private-body", str(result.exception))


if __name__ == "__main__":
    unittest.main()
