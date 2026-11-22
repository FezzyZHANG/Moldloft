from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from unittest.mock import patch

from moldloft.__main__ import main
from moldloft.git_truth import GitTruth
from moldloft.store import Store
from tests import StoreCase


class CliTests(StoreCase):
    def setUp(self):
        super().setUp()
        env = {k: v for k, v in os.environ.items() if not k.startswith("MOLDLOFT_")}
        env["MOLDLOFT_DB"] = str(self.store.db_path)
        config = patch.dict(os.environ, env, clear=True)
        config.start()
        self.addCleanup(config.stop)
        temp = patch("tempfile.tempdir", str(self.root))
        temp.start()
        self.addCleanup(temp.stop)

    def invoke(self, *args, code=0, raw=False):
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = main([str(a) for a in args])
        self.assertEqual(result, code, stderr.getvalue())
        if code == 1:
            return stderr.getvalue()
        return stdout.getvalue() if raw else json.loads(stdout.getvalue())

    def test_execution_dependencies_and_named_notes(self):
        a = self.invoke("new", "Upstream", "--group", "Core", "--priority", "high")["created"]["new:ticket"]
        b = self.invoke("new", "Downstream")["created"]["new:ticket"]
        mid = self.invoke("new", "验收", "--after", a, "--blocks", b, "--parent", a)["created"]["new:ticket"]
        self.invoke("new", "Assumption", "--note", "n-doc", "--kind", "assumption")
        self.assertEqual([r["id"] for r in self.invoke("ready", "--json")], [a])
        self.invoke("done", a)
        self.assertEqual([r["id"] for r in self.invoke("ready", "--json")], [mid])
        self.assertEqual(self.invoke("ls", "--status", "done", "--json")[0]["id"], a)
        self.assertIn("验收", self.invoke("ls", raw=True))
        self.assertEqual(self.invoke("ls", "--notes", "--json")[0]["state"], "proposed")
        self.invoke("new", "Invalid batch", "--after", 99, code=1)
        self.assertEqual(len(self.invoke("ls", "--json")), 3)

    def test_external_editor_and_verify_exit_status(self):
        tid = self.ticket("Before")
        editor = self.root / "test editor.py"
        editor.write_text("from pathlib import Path\nimport sys\np = Path(sys.argv[1])\n"
                          "p.write_bytes(p.read_bytes().replace(b'title: Before', b'title: After'))\n", encoding="utf-8")
        args = [sys.executable, str(editor)]
        cmd = subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)
        self.assertTrue(self.invoke("edit", f"#{tid}", "--editor", cmd)["changed"])
        self.assertEqual(self.store.get_item(tid)["item"]["title"], "After")
        self.checkout()
        self.store.git_truth = GitTruth({"fixture": self.repo})
        self.store.mutate([dict(op="anchor_add", target_type="ticket", target_id=tid,
                                anchor=dict(kind="file", repo="fixture", path=self.file.name))])
        file = self.root / "repos.json"
        file.write_text(json.dumps({"fixture": str(self.repo)}), encoding="utf-8")
        self.assertEqual(self.invoke("verify", "--repos", file, "--target", f"#{tid}", "--json")[0]["status"], "fresh")
        self.file.write_bytes(b"Changed\n")
        self.commit("Update")
        self.assertEqual(self.invoke("--repo", f"fixture={self.repo}", "verify", "--json", code=2)[0]["status"], "drifted")
        self.assertIn("missing", self.invoke("verify", code=2, raw=True))

    def test_anchor_cli_file_lifecycle_honors_graph_and_target(self):
        self.checkout()
        self.ticket(f"`{self.file.name}:2-3`")
        self.store.create_graph("Other", "other")
        def anchor(*args, **kwargs):
            return self.invoke("--repo", f"fixture={self.repo}", "anchor", *args, **kwargs)
        before = self.store.revision()
        suggestion = anchor("suggest", "--target", "#1", "--json")
        self.assertEqual(suggestion, [dict(target_type="ticket", target_id=1, repo="fixture",
                                          path=self.file.name, start_line=2, end_line=3)])
        self.assertIn(f"fixture:{self.file.name}:2-3", anchor("suggest", raw=True))
        self.assertEqual(self.store.revision(), before)
        def add(*args, **kwargs):
            return anchor("add", "#1", "--repo", "fixture", "--path", self.file.name, *args, **kwargs)
        self.assertIn("--lines", add("--lines", "bad", code=1))
        aid = add("--lines", "2-3", "--ref", "HEAD", "--symbol", "demo", "--claim", "Initial")["anchors_created"][0]
        self.assertEqual(anchor("suggest", "--json"), [])
        row, = anchor("ls", "--target", "#1", "--json")
        self.assertEqual((row["id"], row["start_line"], row["end_line"], row["symbol"], row["claim"]),
                         (aid, 2, 3, "demo", "Initial"))
        self.assertIn("ticket:1", anchor("ls", raw=True))
        self.assertEqual(anchor("ls", "--graph", "other", "--json"), [])
        anchor("rm", aid, "--graph", "other", code=1)
        anchor("reanchor", aid, "--graph", "other", code=1)
        self.file.write_bytes(b"new first line\n" + self.file.read_bytes())
        head = self.commit("shift file")
        anchor("reanchor", aid, "--lines", "3-4", "--symbol", "shifted", "--claim", "Reviewed")
        row, = anchor("ls", "--json")
        self.assertEqual((row["pinned_sha"], row["start_line"], row["end_line"], row["symbol"], row["claim"]),
                         (head, 3, 4, "shifted", "Reviewed"))
        anchor("rm", aid)
        self.assertEqual(anchor("ls", "--json"), [])
        self.assertEqual(anchor("suggest", "--json"), suggestion)

    def test_configured_exclusions_and_commit_and_branch_anchors(self):
        self.checkout()
        self.note("n-doc", body=f"`{self.file.name}`")
        path = self.root / "repos.json"
        path.write_text(json.dumps({"fixture": {"path": str(self.repo), "exclude": [self.file.name]}}),
                        encoding="utf-8")
        def add(*args, **kwargs):
            return self.invoke("anchor", "add", "n-doc", "--repo", "fixture", *args, **kwargs)
        with patch.dict(os.environ, {"MOLDLOFT_REPOS": str(path)}):
            self.assertEqual(self.invoke("anchor", "suggest", "--target", "n-doc", "--json"), [])
            self.assertIn("excluded", add("--path", self.file.name, code=1))
            add("--branch", "main")
            add("--commit", "HEAD")
            add("--commit", "HEAD", "--lines", "1-2", code=1)
            def inspect_reload(*args, **kwargs):
                from moldloft.config import resolve_repo_configs
                self.assertEqual(resolve_repo_configs()["fixture"].exclude, (self.file.name,))
            with patch("moldloft.__main__.uvicorn.run", side_effect=inspect_reload):
                self.invoke("serve", "--reload", raw=True)
        branch, commit = self.invoke("anchor", "ls", "--target", "n-doc", "--json")
        self.assertEqual((branch["kind"], branch["ref"]), ("branch", "main"))
        self.assertEqual((commit["kind"], commit["ref"]), ("commit", self.git("rev-parse", "HEAD")))

    def test_scoped_and_full_exports_and_restore(self):
        self.ticket("Default")
        self.store.create_graph("Other", "other")
        self.invoke("new", "--graph", "other", "Other ticket")
        self.assertEqual(self.invoke("--graph", "other", "ls", "--json")[0]["title"], "Other ticket")
        folder = self.root / "all"
        self.assertEqual(self.invoke("export", folder)["graphs"], 2)
        self.assertEqual(self.invoke("--graph", "default", "export", self.root / "one")["graphs"], 1)
        self.assertEqual(self.invoke("export", self.root / "two", "--all-graphs", "--graph", "other")["graphs"], 2)
        db = self.root / "copy.db"
        self.assertEqual(self.invoke("import", "--db", db, folder)["tickets"], 2)
        self.assertIn("not empty", self.invoke("import", folder, code=1))
        self.invoke("import", folder, "--replace")
        self.assertEqual(Store(db).snapshot("other"), self.store.snapshot("other"))

    def test_server_defaults_reload_and_stdio_configuration(self):
        with patch("moldloft.__main__.uvicorn.run") as run, patch("moldloft.__main__.load_env_file") as env:
            self.invoke(raw=True)
            env.assert_called_once_with()
            self.assertEqual(run.call_args.kwargs["port"], 8787)
            self.assertEqual(run.call_args.kwargs["host"], "127.0.0.1")
            self.assertEqual(run.call_args.kwargs["timeout_graceful_shutdown"], 5)
            self.invoke("--listen", "--port", 8800, "--reload", raw=True)
            self.assertTrue(run.call_args.kwargs["reload"])
            self.assertEqual(run.call_args.kwargs["host"], "0.0.0.0")
        with patch("moldloft.__main__.run_stdio") as stdio:
            self.invoke("mcp", raw=True)
            self.assertEqual(stdio.call_args.args[0].db_path, self.store.db_path)

    def test_demo_builds_and_removes_realistic_fixture(self):
        paths = []

        def observe(app, **options):
            db = Path(os.environ["MOLDLOFT_DB"])
            roots = json.loads(Path(os.environ["MOLDLOFT_REPOS"]).read_text(encoding="utf-8"))
            paths.extend([db, Path(roots["example"])])
            demo = Store(db, GitTruth(roots))
            self.assertEqual({r["status"] for r in demo.verify_ground_truth()}, {"fresh", "moved", "drifted"})
            self.assertEqual(demo.snapshot()["notes"][0]["kind"], "assumption")
            self.assertEqual(options["port"], 8791)

        with patch("moldloft.__main__.uvicorn.run", side_effect=observe):
            self.invoke("demo", raw=True)
        self.assertTrue(paths)
        self.assertFalse(any(p.exists() for p in paths))
        self.assertEqual(self.store.revision(), 0)
