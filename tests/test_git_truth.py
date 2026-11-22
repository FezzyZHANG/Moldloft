from hashlib import sha256
from unittest.mock import patch

from moldloft import git_truth
from moldloft.errors import DomainError
from moldloft.git_truth import GitTruth, safe_path
from tests import StoreCase


class GitTests(StoreCase):
    def setUp(self):
        super().setUp()
        self.checkout()
        self.truth = GitTruth({"fixture": self.repo})
        self.store.git_truth = self.truth
        self.spec = dict(repo="fixture", kind="file", path=self.file.name, start_line=2, end_line=3)

    def test_pin_exact_bytes_and_excerpts(self):
        self.file.write_bytes("préface\r\nsecond\r\nlast".encode())
        sha = self.commit("UTF-8 and CRLF")
        anchor = self.truth.pin(dict(self.spec, symbol="entry", claim="text"))
        self.assertEqual(anchor["pinned_sha"], sha)
        self.assertEqual(anchor["ref"], sha)
        self.assertEqual(anchor["excerpt"], "second\r\nlast")
        self.assertEqual(anchor["content_hash"], sha256(b"second\r\nlast").hexdigest())
        default = self.truth.pin(dict(repo="fixture", kind="file", path=self.file.name))
        self.assertEqual((default["start_line"], default["end_line"]), (1, 3))
        self.file.write_bytes(b"z" * 4100 + b"\n" + b"line\n" * 500)
        self.commit("long file")
        bounded = self.truth.pin(dict(repo="fixture", kind="file", path=self.file.name))
        self.assertEqual(bounded["end_line"], 200)
        self.assertEqual(len(bounded["excerpt"]), 4000)
        self.assertNotEqual(bounded["content_hash"], sha256(bounded["excerpt"].encode()).hexdigest())
        with self.assertRaises(DomainError):
            self.truth.pin(dict(self.spec, start_line=1, end_line=501))

    def test_fresh_dirty_moved_drifted_and_deleted(self):
        pinned = self.truth.pin(self.spec)
        self.assertEqual(self.truth.verify(pinned)["status"], "fresh")
        (self.repo / "untracked.txt").write_bytes(b"untracked")
        self.assertEqual(self.truth.verify(pinned)["status"], "fresh")
        self.file.write_bytes(b"new\nalpha\nbravo\ncharlie\ndelta\n")
        index = (self.repo / ".git" / "index").read_bytes()
        dirty = self.truth.verify(pinned)
        self.assertTrue(dirty["working_dirty"])
        self.assertEqual(dirty["status"], "dirty")
        self.assertEqual((self.repo / ".git" / "index").read_bytes(), index)
        head = self.commit("insert a line")
        moved = self.truth.verify(pinned)
        self.assertEqual(moved["status"], "moved")
        self.assertEqual(moved["current_range"], [3, 4])
        self.assertEqual(moved["suggested_pin"], dict(ref=head, start_line=3, end_line=4))
        self.file.write_bytes(b"new\nalpha\nBRAVO\ncharlie\ndelta\n")
        self.commit("change span")
        drifted = self.truth.verify(pinned)
        self.assertEqual(drifted["status"], "drifted")
        self.assertIn("-bravo", drifted["diff"])
        self.assertIn("+BRAVO", drifted["diff"])
        self.file.unlink()
        self.commit("delete")
        self.assertEqual(self.truth.verify(pinned)["status"], "missing")
        self.assertEqual(GitTruth().verify(pinned)["status"], "missing")

    def test_commit_branch_and_refs(self):
        sha = self.git("rev-parse", "HEAD")
        self.git("branch", "review/topic")
        commit = self.truth.pin(dict(repo="fixture", kind="commit"))
        branch = self.truth.pin(dict(repo="fixture", kind="branch", ref="refs/heads/review/topic"))
        self.assertEqual(commit["ref"], sha)
        self.assertEqual(branch["ref"], "review/topic")
        for pin in (commit, branch):
            self.assertEqual(pin["content_hash"], sha)
            self.assertRegex(pin["excerpt"], r"^\d{4}-\d\d-\d\d initial$")
            self.assertEqual(self.truth.verify(pin)["status"], "fresh")
        refs = self.truth.refs("fixture")
        self.assertEqual(refs["head"], sha)
        self.assertIn("main", refs["branches"])
        self.assertEqual(refs["commits"][0]["sha"], sha)
        self.file.write_bytes(b"changed")
        self.assertEqual(self.truth.verify(commit)["status"], "dirty")
        self.commit("second")
        self.git("branch", "-f", "review/topic", "HEAD")
        self.assertIn("1 commits", self.truth.verify(branch)["message"])
        self.assertEqual(self.truth.verify(branch)["status"], "drifted")
        self.assertEqual(self.truth.verify(commit)["status"], "fresh")
        self.git("branch", "-D", "review/topic")
        self.assertEqual(self.truth.verify(branch)["status"], "missing")
        self.git("switch", "--orphan", "unrelated")
        self.file.write_bytes(b"separate history")
        self.commit("unrelated")
        self.assertEqual(self.truth.verify(commit)["status"], "drifted")

    def test_newline_only_drift_and_diff_limit(self):
        self.file.write_bytes(b"alpha")
        self.commit("no final newline")
        pin = self.truth.pin(dict(self.spec, start_line=1, end_line=1))
        self.file.write_bytes(b"alpha\n")
        self.commit("final newline")
        checked = self.truth.verify(pin)
        self.assertEqual(checked["status"], "drifted")
        self.assertIn("No newline at end of file", checked["diff"])
        self.file.write_bytes(b"before\n" * 200)
        self.commit("old span")
        pin = self.truth.pin(dict(self.spec, start_line=1, end_line=200))
        self.file.write_bytes(b"after\n" * 200)
        self.commit("new span")
        self.assertEqual(len(self.truth.verify(pin)["diff"].splitlines()), 120)

    def test_pin_validation_and_tracked_regular_blobs_only(self):
        for path in ("", "/abs", "C:drive", "../file", "x/.GiT/config", "x/.env.local", "x/p.PEM",
                     "client.key", "keys/id_rsa", "id_ed25519", "a\0b", "a\rb", "a\nb", "x" * 1001):
            with self.subTest(path=path), self.assertRaises(DomainError):
                safe_path(path)
        for invalid in (dict(ref="--all"), dict(ref="HEAD~1"), dict(ref="missing"), dict(repo="unknown"),
                        dict(kind="invalid"), dict(symbol="s" * 161), dict(claim="c" * 501),
                        dict(end_line=200), dict(start_line=True), dict(kind="branch", ref="main")):
            with self.subTest(invalid=invalid), self.assertRaises(DomainError):
                self.truth.pin(dict(self.spec, **invalid))
        for name, data in (("binary", b"a\0b"), ("encoding", b"\xff"), ("large", b"a" * 500_001)):
            (self.repo / name).write_bytes(data)
        self.commit("invalid file types")
        # Build a real tracked symlink even on Windows hosts without symlink privileges.
        oid = self.git("hash-object", "-w", "--stdin", data=self.file.name.encode())
        self.git("update-index", "--add", "--cacheinfo", f"120000,{oid},pointer")
        self.git("commit", "-m", "symlink")
        for name in ("binary", "encoding", "large", "pointer", "untracked"):
            with self.subTest(name=name), self.assertRaises(DomainError):
                self.truth.pin(dict(self.spec, path=name, start_line=1, end_line=1))
        subdir = self.repo / "subdir"
        (subdir / ".git").mkdir(parents=True)
        with self.assertRaises(DomainError):
            GitTruth({"nested": subdir}).refs("nested")

    def test_exclusions_are_repo_relative_case_insensitive_and_segment_bounded(self):
        names = ["Docs/hidden.md", "private/nested.txt", "draft.md", "draft.md.bak", "Docs2/ok.md"]
        for name in names:
            path = self.repo / name
            path.parent.mkdir(exist_ok=True)
            path.write_text("tracked", encoding="utf-8")
        self.commit("documents")
        truth = GitTruth({"fixture": {"path": self.repo, "exclude": ["dOcS/", "PRIVATE"]}, "other": self.repo},
                         excludes={"fixture": ("DRAFT.md",)})
        for path in names[:3]:
            spec = dict(repo="fixture", kind="file", path=path)
            with self.subTest(path=path), self.assertRaisesRegex(DomainError, "excluded"):
                truth.pin(spec)
            pin = truth.pin(dict(spec, repo="other"))
            result = truth.verify(dict(pin, repo="fixture"))
            self.assertEqual(result["status"], "missing")
            self.assertIn("excluded", result["message"])
        suggested = truth.suggest_paths(" ".join(names))
        self.assertEqual([(s["repo"], s["path"]) for s in suggested],
                         [("fixture", n) for n in names[3:]] + [("other", n) for n in names])

    def test_markdown_suggestions_use_regular_utf8_head_blobs_and_valid_ranges(self):
        for name, data in (("entry.py", b"1\n2\n3"), ("binary.md", b"a\0b"), ("other.md", b"\xff"),
                           (".env.local", b"secret"), ("large.md", b"z" * 500_001)):
            (self.repo / name).write_bytes(data)
        self.commit("candidate paths")
        oid = self.git("hash-object", "-w", "--stdin", data=b"entry.py")
        self.git("update-index", "--add", "--cacheinfo", f"120000,{oid},pointer.md")
        self.git("commit", "-m", "symlink")
        (self.repo / "loose.md").write_bytes(b"untracked")
        (self.repo / "entry.py").unlink()
        self.file.write_bytes(b"shorter in checkout")
        before = (self.repo / ".git" / "index").read_bytes()
        text = ("Review [the file](<read me.txt:2-3>) and ./entry.py:3. Also `read me.txt`, entry.py:1, "
                "binary.md other.md .env.local large.md pointer.md loose.md missing.md")
        self.assertEqual(self.truth.suggest_paths(text), [
            dict(repo="fixture", path=self.file.name, start_line=2, end_line=3),
            dict(repo="fixture", path="entry.py", start_line=3, end_line=3)])
        self.assertEqual(self.truth.suggest_paths("../entry.py /entry.py https://host/entry.py entry.py:4 "
                                                 "entry.py:0 entry.py:3-1 entry.py:1-501"), [])
        self.assertEqual(before, (self.repo / ".git" / "index").read_bytes())

    def test_suggestions_cap_per_item_after_excluding_existing_pins(self):
        names = [f"file-{i}.md" for i in range(24)]
        for name in names:
            (self.repo / name).write_bytes(b"contents")
        self.commit("many paths")
        tid = self.ticket("Files", tech_notes=" ".join(names))
        self.store.mutate([dict(op="anchor_add", target_type="ticket", target_id=tid,
                                anchor=dict(kind="file", repo="fixture", path=name)) for name in names[:3]])
        self.assertEqual([r["path"] for r in self.store.suggest_anchors(target=f"#{tid}")], names[3:23])

    def test_bulk_suggestions_limit_work_before_git_reads(self):
        self.store.mutate([dict(op="ticket_create", temp_id=f"new:t{i}", fields=dict(title=f"Task {i}"))
                           for i in range(100)])
        last = self.ticket("Last")
        with patch.object(self.truth, "suggest_paths", return_value=[]) as suggest:
            self.assertEqual(self.store.suggest_anchors(), [])
            self.assertEqual(suggest.call_count, 100)
            self.assertNotIn("Last", suggest.call_args.args[0])
            self.store.suggest_anchors(target=last)
            self.assertIn("Last", suggest.call_args.args[0])

    def test_store_suggestions_scope_target_status_graph_and_existing_anchor(self):
        mention = f"`{self.file.name}:2`"
        first = self.ticket("Open", description=mention)
        progress = self.ticket(mention, status="in_progress")
        done = self.ticket("Done", tech_notes=mention, status="done")
        self.ticket(mention, status="closed")
        self.note("n-doc", summary=mention)
        self.store.create_graph("Other", "other")
        self.store.mutate([dict(op="ticket_create", temp_id="new:t", fields=dict(title=mention))], graph_id="other")
        with self.assertRaises(DomainError):
            self.store.suggest_anchors("other", "n-doc")
        with self.assertRaises(DomainError):
            self.store.suggest_anchors("missing")
        self.store.mutate([dict(op="anchor_add", target_type="ticket", target_id=first, anchor=self.spec)])
        before = self.store.snapshot()
        expected = dict(repo="fixture", path=self.file.name, start_line=2, end_line=2)
        self.assertEqual(self.store.suggest_anchors(), [dict(expected, target_type="ticket", target_id=progress)])
        for kind, key in (("note", "n-doc"), ("ticket", done)):
            self.assertEqual(self.store.suggest_anchors(target=key), [dict(expected, target_type=kind, target_id=key)])
        self.assertEqual(self.store.snapshot(), before)

    def test_anchors_on_both_target_types_and_explicit_reanchor(self):
        tid = self.ticket("Ticket")
        self.note("n-doc")
        ops = [dict(op="anchor_add", target_type=kind, target_id=key, anchor=self.spec)
               for kind, key in (("ticket", tid), ("note", "n-doc"))]
        a, b = self.store.mutate(ops)["anchors_created"]
        pinned = self.store.snapshot()
        self.file.write_bytes(b"intro\nalpha\nbravo\ncharlie\ndelta\n")
        self.commit("header")
        checked = self.store.verify_ground_truth()
        self.assertEqual([r["status"] for r in checked], ["moved", "moved"])
        self.assertEqual(self.store.snapshot(), pinned)
        self.store.reanchor(a, **checked[0]["suggested_pin"], claim="Reviewed")
        self.assertEqual(self.store.get_item(tid)["anchors"][0]["claim"], "Reviewed")
        self.assertEqual(self.store.verify_ground_truth(target=tid)[0]["status"], "fresh")
        self.assertEqual(self.store.verify_ground_truth(target="n-doc")[0]["status"], "moved")
        self.store.create_graph("Other", "other")
        self.store.move_ticket(tid, "other")
        self.assertEqual(self.store.verify_ground_truth("other")[0]["anchor_id"], a)
        self.store.mutate([dict(op="anchor_remove", anchor_id=a)], graph_id="other")
        self.assertFalse(self.store.snapshot("other")["anchors"])
        self.assertEqual(self.store.get_item("n-doc")["anchors"][0]["id"], b)
        self.store.mutate([dict(op="note_delete", note_id="n-doc")])
        self.assertFalse(self.store.snapshot()["anchors"])

    def test_batch_reuses_reads_and_cache_expires(self):
        with patch.object(git_truth, "monotonic", return_value=0) as clock:
            pin = self.truth.pin(self.spec)
            with patch.object(git_truth, "_run", wraps=git_truth._run) as run:
                results = self.truth.verify_many([pin] * 12)
                self.assertEqual([r["status"] for r in results], ["fresh"] * 12)
                self.assertEqual(run.call_count, 2)  # One HEAD read and one worktree status.
                self.truth.refs("fixture")
                calls = run.call_count
                self.truth.refs("fixture")
                self.assertEqual(run.call_count, calls)
                clock.return_value = 4
                self.truth.refs("fixture")
                self.assertGreater(run.call_count, calls)
                run.reset_mock()
                self.truth.verify(pin)
                self.assertTrue(any("cat-file" in call.args for call in run.call_args_list))
