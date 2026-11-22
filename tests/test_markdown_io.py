from pathlib import Path
from unittest.mock import patch

import yaml

from moldloft.errors import DomainError
from moldloft.git_truth import GitTruth
from moldloft.markdown_io import (edit_in_editor, export_workspace, import_workspace, parse_note_md,
                                  parse_ticket_md, render_note_md, render_ticket_md)
from moldloft.store import Store
from tests import StoreCase


def tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


class MarkdownTests(StoreCase):
    def setUp(self):
        super().setUp()
        temp = patch("tempfile.tempdir", str(self.root))
        temp.start()
        self.addCleanup(temp.stop)

    def test_lossless_bodies_links_and_metadata(self):
        text = "\n# 描述\n## Technical notes\n---\n<!-- moldloft:tech_notes -->\n# 证据\r\n\n"
        tid = self.ticket("设计: #1", description=text, tech_notes=text + "  ")
        ticket = self.store.get_item(tid)["item"]
        md = render_ticket_md(ticket)
        self.assertNotIn("\r", md)
        self.assertIn("设计", md)
        meta, fields = parse_ticket_md(md)
        self.assertEqual(meta["version"], 1)
        self.assertEqual(fields, {k: ticket[k] for k in fields})
        self.assertEqual(parse_ticket_md(md.split("\n<!-- moldloft:tech_notes -->\n")[0])[1]["tech_notes"], "")
        nid = self.note("n-design", body=text, summary="# 总结\x85下一行", in_graph=False)
        note = self.store.get_item(nid)["item"]
        link = dict(source_note_id=nid, relation="relates", target_type="ticket", target_id=str(tid))
        meta, fields = parse_note_md(render_note_md(note, [link]))
        self.assertEqual(meta["id"], nid)
        self.assertEqual(fields.pop("links"), [dict(relation="relates", target_type="ticket", target_id=tid)])
        self.assertEqual(fields, {k: note[k] for k in fields})

    def test_editor_noop_ticket_and_note_save(self):
        tid = self.ticket("First", description="旧")
        paths = []
        same = edit_in_editor(self.store, str(tid), editor=paths.append)
        self.assertEqual(same, dict(changed=False, target=str(tid)))
        self.assertFalse(paths[0].exists())
        self.assertEqual(self.store.revision(), 1)

        def update(path):
            paths.append(path)
            path.write_bytes(path.read_bytes().replace("旧".encode(), "新".encode()).replace(b"group_name: ''", b"group_name: Core"))

        self.assertTrue(edit_in_editor(self.store, str(tid), editor=update)["changed"])
        self.assertFalse(paths[-1].exists())
        self.assertEqual(self.store.get_item(tid)["item"]["group_name"], "Core")
        self.assertEqual(self.store.get_item(tid)["item"]["description"], "新")
        self.note("n-first", kind="assumption", state="adopted", links=[
            dict(relation="assumes", target_type="ticket", target_id=tid)])
        self.note("n-peer", links=[dict(relation="relates", target_type="note", target_id="n-first")])
        edit_in_editor(self.store, "n-first", editor=lambda path: path.write_bytes(path.read_bytes() + b"revised"))
        note = self.store.get_item("n-first")
        self.assertTrue(note["item"]["adoption_stale"])
        self.assertEqual(len(note["neighbors"]), 2)

    def test_editor_preserves_parse_failure_and_conflicting_drafts(self):
        tid = self.ticket("Base")

        def stale(path):
            path.write_bytes(path.read_bytes().replace(b"title: Base", b"title: Draft"))
            self.store.patch_ticket(tid, {"title": "Concurrent"}, version=1)

        with self.assertRaises(DomainError) as conflict:
            edit_in_editor(self.store, str(tid), editor=stale)
        self.assertEqual(conflict.exception.status, 409)
        self.assertEqual(conflict.exception.details["current"]["title"], "Concurrent")
        self.assertIn(b"title: Draft", Path(conflict.exception.details["draft_path"]).read_bytes())

        def structural(path):
            path.write_bytes(path.read_bytes().replace(b"group_name: ''", b"group_name: Edited"))
            self.ticket("Unrelated")

        with self.assertRaisesRegex(DomainError, "revision") as conflict:
            edit_in_editor(self.store, str(tid), editor=structural)
        self.assertTrue(Path(conflict.exception.details["draft_path"]).exists())
        for bad in (b"---\ntitle: [\n---\nbody", b"\xff", b"plain text"):
            with self.subTest(bad=bad), self.assertRaises(DomainError) as failed:
                edit_in_editor(self.store, str(tid), editor=lambda path: path.write_bytes(bad))
            self.assertEqual(Path(failed.exception.details["draft_path"]).read_bytes(), bad)
        self.assertEqual(self.store.get_item(tid)["item"]["title"], "Concurrent")

    def test_multi_graph_archive_roundtrip_offline(self):
        self.checkout()
        self.store.git_truth = GitTruth({"fixture": self.repo})
        self.store.create_graph("第二", "other")
        self.store.mutate([dict(op="note_create", id="n-other", fields=dict(title="Elsewhere", in_graph=False))],
                          graph_id="other")
        ids = self.store.mutate([
            dict(op="ticket_create", temp_id="new:child", fields=dict(title="Child", parent_id="new:parent",
                 description="\n## 描述\n", tech_notes="细节\r\n", priority="high")),
            dict(op="ticket_create", temp_id="new:parent", fields=dict(title="Parent")),
            dict(op="edge_add", blocker_id="new:parent", blocked_id="new:child"),
        ])["created"]
        self.note("n-window", kind="assumption", state="adopted", links=[
            dict(relation="assumes", target_type="ticket", target_id=ids["new:child"])])
        self.store.patch_note("n-window", {"body": "新结论"}, version=1)
        self.store.mutate([dict(op="anchor_add", target_id="n-window", target_type="note",
                                anchor=dict(repo="fixture", kind="file", path=self.file.name))])
        proposal = dict(operations=[dict(op="ticket_update", ticket_id=ids["new:parent"], fields=dict(title="Updated"))],
                        summary="Edit", rationale="Because", base_revision=self.store.revision(), request_id="first")
        applied = self.store.submit_proposal(**proposal)["id"]
        self.store.apply_proposal(applied, note="Reviewed")
        last = self.ticket("Deleted")
        self.store.mutate([dict(op="ticket_delete", ticket_id=last)])
        self.store.submit_proposal(**dict(proposal, base_revision=self.store.revision(), request_id="second"))
        first, second = self.root / "one", self.root / "two"
        export_workspace(self.store, first)
        restored_path = self.root / "restored.db"
        with patch.object(GitTruth, "pin", side_effect=AssertionError("Restore must use saved Git snapshots")):
            import_workspace(restored_path, first)
        restored = Store(restored_path)
        export_workspace(restored, second)
        self.assertEqual(tree(first), tree(second))
        self.assertTrue(all(b"\r" not in data for data in tree(first).values()))
        self.assertEqual(restored.snapshot(), self.store.snapshot())
        self.assertEqual(restored.get_proposal(applied), self.store.get_proposal(applied))
        self.assertEqual(restored.search("结论")[0]["item_id"], "n-window")
        new = restored.mutate([dict(op="ticket_create", temp_id="new:t", fields=dict(title="Next"))])
        self.assertGreater(new["created"]["new:t"], last)
        with self.assertRaisesRegex(DomainError, "not empty") as occupied:
            import_workspace(restored_path, first)
        self.assertEqual(occupied.exception.status, 409)
        import_workspace(restored_path, first, replace=True)
        self.assertEqual(restored.snapshot(), self.store.snapshot())

    def test_invalid_archive_does_not_replace_database(self):
        tid = self.ticket("Keep")
        folder = self.root / "out"
        export_workspace(self.store, folder)
        before = self.store.snapshot()
        edges = folder / "graphs/default/edges.yaml"
        edges.write_bytes(yaml.safe_dump([[tid, tid]]).encode())
        with self.assertRaisesRegex(DomainError, "cycle"):
            import_workspace(self.store.db_path, folder, replace=True)
        self.assertEqual(self.store.snapshot(), before)
        edges.write_bytes(b"[]\n")
        manifest = folder / "manifest.yaml"
        data = yaml.safe_load(manifest.read_bytes())
        data["sequences"]["tickets"] = 0
        manifest.write_bytes(yaml.safe_dump(data).encode())
        with self.assertRaisesRegex(DomainError, "sequence"):
            import_workspace(self.store.db_path, folder, replace=True)
        self.assertEqual(self.store.snapshot(), before)

    def test_repeated_export_and_interrupted_restore(self):
        folder = self.root / "archive"
        tid = self.ticket("Remove")
        self.store.create_graph("Other", "Other")
        export_workspace(self.store, folder)
        self.assertTrue((folder / "graphs/Other/edges.yaml").exists())
        self.store.delete_graph("Other")
        self.store.mutate([dict(op="ticket_delete", ticket_id=tid)])
        self.note("n-new", body="Replacement")
        export_workspace(self.store, folder)
        fresh = self.root / "fresh"
        export_workspace(self.store, fresh)
        self.assertEqual(tree(folder), tree(fresh))
        self.ticket("Keep on rollback")
        before = self.store.snapshot()
        with patch("moldloft.markdown_io.rebuild_fts", side_effect=RuntimeError("Interrupted")):
            with self.assertRaisesRegex(RuntimeError, "Interrupted"):
                import_workspace(self.store.db_path, folder, replace=True)
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.store.search("rollback")[0]["title"], "Keep on rollback")
