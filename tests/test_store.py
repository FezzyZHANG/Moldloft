from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from moldloft.config import default_db_path, load_env_file, RepoConfig, resolve_db_path, resolve_repo_configs, resolve_repos
from moldloft.db import connect, init_db, rebuild_fts, utc_now, write_tx
from moldloft.errors import DomainError
from moldloft.reducer import reduce_graph
from tests import StoreCase


def link(kind, key, relation="assumes"):
    return dict(target_type=kind, target_id=key, relation=relation)


class DatabaseTests(StoreCase):
    def test_initialization_and_transactions(self):
        path = self.store.db_path
        self.assertEqual(init_db(path), path)
        self.assertRegex(utc_now(), r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        with closing(connect(path)) as conn:
            for pragma, expected in (("journal_mode", "wal"), ("foreign_keys", 1), ("busy_timeout", 5000), ("user_version", 1)):
                self.assertEqual(conn.execute(f"PRAGMA {pragma}").fetchone()[0], expected)
            with self.assertRaises(ValueError), write_tx(conn):
                conn.execute("UPDATE graphs SET name='Transient'")
                raise ValueError()
            self.assertEqual(conn.execute("SELECT name FROM graphs").fetchone()["name"], "Default")
            conn.execute("PRAGMA user_version=2")
        with self.assertRaisesRegex(DomainError, "version 2"):
            init_db(path)

    def test_explicit_configuration_and_precedence(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.root)}, clear=True):
            path = self.root / ".env"
            path.write_text("# comment\n\nUSER_VALUE='上游'\nOTHER=\"file=value\"\n", encoding="utf-8")
            os.environ["USER_VALUE"] = "process"
            with patch("pathlib.Path.read_text", side_effect=AssertionError("unexpected discovery")):
                load_env_file()
            os.environ["MOLDLOFT_ENV_FILE"] = str(path)
            load_env_file()
            self.assertEqual(os.environ["USER_VALUE"], "process")
            self.assertEqual(os.environ["OTHER"], "file=value")
            self.assertEqual(resolve_db_path(), default_db_path().resolve())
            os.environ["MOLDLOFT_DB"] = str(self.root / "a")
            self.assertEqual(resolve_db_path(), self.root / "a")
            self.assertEqual(resolve_db_path(self.root / "b"), self.root / "b")
            config = self.root / "repos.json"
            config.write_text(json.dumps({"first": str(self.root), "second": str(self.root)}), encoding="utf-8")
            os.environ["MOLDLOFT_REPOS"] = str(config)
            self.assertEqual(resolve_repos({"first": self.root / "new"}),
                             {"first": self.root / "new", "second": self.root})
            for invalid in ({"-bad": self.root}, {"x" * 41: self.root}, {"valid": Path("relative")}):
                with self.subTest(invalid=invalid), self.assertRaises(DomainError):
                    resolve_repos(invalid)

    def test_repository_file_accepts_mixed_configs_and_validates_rules(self):
        config = self.root / "repos.json"
        rules = ["Docs/", "private/file.md"]
        config.write_text(json.dumps({"simple": str(self.root), "filtered": {"path": str(self.root), "exclude": rules}}),
                          encoding="utf-8")
        with patch.dict(os.environ, {"MOLDLOFT_REPOS": str(config)}):
            self.assertEqual(resolve_repos(), {"simple": self.root, "filtered": self.root})
            self.assertEqual(resolve_repo_configs()["filtered"], RepoConfig(self.root, tuple(rules)))
            self.assertEqual(resolve_repo_configs({"filtered": self.root / "new"})["filtered"],
                             RepoConfig(self.root / "new", tuple(rules)))
            for invalid in ("dir/", ["/absolute"], ["../escape"], [""], [None], None):
                with self.subTest(invalid=invalid), self.assertRaises(DomainError):
                    resolve_repo_configs({"filtered": {"path": self.root, "exclude": invalid}})


class GraphTests(StoreCase):
    def test_graph_crud_and_isolation(self):
        self.assertEqual([g["id"] for g in self.store.list_graphs()], ["default"])
        other = self.store.create_graph("Second", "other")["id"]
        with self.assertRaises(DomainError):
            self.store.create_graph("sEConD")
        self.assertEqual(self.store.rename_graph(other, "Renamed")["name"], "Renamed")
        tid = self.ticket("Home")
        self.store.mutate([dict(op="note_create", id="n-other", fields=dict(title="Over there"))], graph_id=other)
        with self.assertRaises(DomainError):
            self.store.get_item(tid, other)
        self.assertEqual(self.store.search("there"), [])
        self.store.delete_graph(other)
        self.assertEqual(self.store.get_item(tid)["item"]["title"], "Home")
        with self.assertRaises(DomainError):
            self.store.get_graph(other)
        with self.assertRaises(DomainError):
            self.store.delete_graph("default")

    def test_both_dags_cycle_paths_and_independence(self):
        a = self.ticket("Root", group_name="Core")
        b = self.ticket("Child", parent_id=a, group_name="ignored")
        self.store.mutate([dict(op="edge_add", blocker_id=b, blocked_id=a)])
        for op, label in ((dict(op="ticket_update", ticket_id=a, fields=dict(parent_id=b)), "Containment"),
                          (dict(op="edge_add", blocker_id=a, blocked_id=b), "Dependency")):
            before = self.store.snapshot()
            with self.assertRaisesRegex(DomainError, label) as caught:
                self.store.mutate([op])
            self.assertEqual(caught.exception.status, 409)
            cycle = caught.exception.details["path"]
            self.assertEqual(set(cycle), {a, b})
            self.assertEqual(cycle[0], cycle[-1])
            self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.store.get_item(b)["item"]["group_name"], "Core")
        self.store.patch_ticket(a, {"group_name": "New"}, version=2, revision=self.store.revision())
        self.assertEqual(self.store.get_item(b)["item"]["group_name"], "New")

    def test_forward_refs_delete_reparents_and_cleans_relations(self):
        batch = [
            dict(op="note_create", id="n-e", fields=dict(title="Evidence", kind="evidence",
                 links=[link("ticket", "new:mid", "supports")])),
            dict(op="ticket_create", temp_id="new:kid", fields=dict(title="Kid", parent_id="new:mid")),
            dict(op="ticket_create", temp_id="new:mid", fields=dict(title="Mid", parent_id="new:root")),
            dict(op="ticket_create", temp_id="new:root", fields=dict(title="Root", group_name="G")),
            dict(op="edge_add", blocker_id="new:mid", blocked_id="new:kid"),
        ]
        before, original = self.store.snapshot(), deepcopy(batch)
        preview = reduce_graph(before, batch)
        self.assertEqual(batch, original)
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(preview, reduce_graph(before, batch))
        ids = self.store.mutate(batch)["created"]
        kid, mid, root = (ids["new:" + name] for name in ("kid", "mid", "root"))
        self.store.mutate([dict(op="ticket_delete", ticket_id=mid)])
        graph = self.store.snapshot()
        self.assertEqual(graph["edges"], [])
        self.assertEqual(graph["note_links"], [])
        self.assertEqual(self.store.get_item(kid)["item"]["parent_id"], root)
        self.assertEqual(self.store.search("Mid"), [])

    def test_move_subtree_and_blocking_cross_edges(self):
        self.store.create_graph("Other", "g2")
        parent = self.ticket("Parent")
        root = self.ticket("Move", parent_id=parent)
        child = self.ticket("Nested", parent_id=root)
        self.note("n-a", kind="assumption", links=[link("ticket", child)])
        inside = dict(op="edge_add", blocker_id=root, blocked_id=child)
        outside = dict(op="edge_add", blocker_id=parent, blocked_id=child)
        self.store.mutate([inside, outside])
        before = self.store.snapshot()
        with self.assertRaises(DomainError) as caught:
            self.store.move_ticket(root, "g2")
        self.assertEqual(caught.exception.details["edges"], [dict(blocker_id=parent, blocked_id=child)])
        self.assertEqual(self.store.snapshot(), before)
        self.store.mutate([dict(outside, op="edge_remove")])
        self.assertEqual(self.store.move_ticket(root, "g2")["moved"], [root, child])
        moved = self.store.snapshot("g2")
        self.assertEqual(moved["edges"], [dict(blocker_id=root, blocked_id=child)])
        self.assertIsNone(self.store.get_item(root)["item"]["parent_id"])
        self.assertEqual(self.store.get_item(child)["item"]["parent_id"], root)
        self.assertEqual(self.store.snapshot()["note_links"], [])
        self.assertEqual(self.store.search("Nest"), [])
        self.assertEqual(self.store.search("Nest", "g2")[0]["item_id"], str(child))

    def test_premises_propagate_and_only_done_unblocks(self):
        a, b = self.ticket("A"), self.ticket("B")
        self.store.mutate([dict(op="edge_add", blocker_id=a, blocked_id=b)])
        self.note("n-direct", kind="assumption", state="adopted", links=[link("ticket", a)])
        self.note("n-chain", kind="assumption", links=[link("note", "n-direct")])
        self.note("n-hidden", kind="assumption", in_graph=False, links=[link("ticket", b)])
        row = self.store.get_item(b)["item"]
        self.assertEqual(row["assumptions"], ["n-chain", "n-direct"])
        self.assertEqual(row["review_notes"], ["n-chain"])
        self.assertEqual(row["blockers"], [a])
        self.store.patch_ticket(a, {"status": "closed"}, version=2)
        self.assertFalse(self.store.get_ready_work())
        self.store.patch_ticket(a, {"status": "done"}, version=3)
        self.assertEqual([t["id"] for t in self.store.get_ready_work()], [b])
        self.store.patch_note("n-direct", {"body": "changed", "state": "adopted"}, version=1)
        self.assertTrue(self.store.get_item("note:n-direct")["item"]["adoption_stale"])
        self.assertEqual(self.store.get_item(b)["item"]["review_notes"], ["n-chain", "n-direct"])
        self.store.patch_note("n-direct", {"state": "adopted"}, version=2)
        self.assertFalse(self.store.get_item("n-direct")["item"]["adoption_stale"])
        self.store.patch_note("n-direct", {"in_graph": False}, version=3)
        self.assertEqual(self.store.get_item(b)["item"]["assumptions"], [])

    def test_note_relation_rules_targets_and_deletion(self):
        self.note("n-first")
        self.note("n-second", links=[link("note", "n-first", "relates")])
        for links in ([link("note", "n-first", "supports")], [link("note", "n-first")],
                      [link("note", "n-second", "relates")], [link("note", "n-gone", "relates")],
                      [link("note", "n-first", "relates")] * 2):
            with self.subTest(links=links), self.assertRaises(DomainError):
                self.store.patch_note("n-second", {"links": links}, version=1)
        self.assertEqual(self.store.get_item("n-first")["neighbors"][0]["target_id"], "n-second")
        self.store.mutate([dict(op="note_delete", note_id="n-first")])
        self.assertFalse(self.store.snapshot()["note_links"])
        self.assertEqual(self.store.get_item("n-second")["item"]["version"], 2)

    def test_exact_targets_rank_first_across_graph_and_kind_filters(self):
        self.note("n-other", body="ticket:1 #1 n-slug")
        self.ticket("First ticket")
        self.note("n-slug", body="Some other text")
        for query, key in (("#1", 1), ("ticket:1", 1), ("1", 1), ("n-slug", "n-slug")):
            with self.subTest(query=query):
                rows = self.store.search(f" {query} ", "default")
                self.assertEqual(rows[0]["item_id"], str(key))
                self.assertIn("n-other", [r["item_id"] for r in rows[1:]])
                self.assertEqual(self.store.get_item(query, "default")["item"]["id"], key)
        self.assertEqual(self.store.search("#1", kind="note")[0]["item_id"], "n-other")
        self.store.create_graph("Second", "other")
        self.assertEqual(self.store.search("#1", "other"), [])
        for invalid in ("#0", "##1", "#-1", "#9223372036854775808", "#1x"):
            with self.assertRaises(DomainError):
                self.store.get_item(invalid)

    def test_search_escapes_queries_rebuilds_and_updates(self):
        tid = self.ticket("Searchable", tech_notes="gear\nwheel")
        self.note("n-idea", body="gear train")
        for value in (tid, str(tid), f"ticket:{tid}"):
            self.assertEqual(self.store.get_item(value)["item"]["id"], tid)
        self.assertEqual(len(self.store.search('gear"*')), 2)
        self.assertEqual(self.store.search("gear", kind="note")[0]["item_id"], "n-idea")
        self.assertEqual(self.store.search("note:n-idea")[0]["item_id"], "n-idea")
        self.assertEqual(self.store.search(str(tid))[0]["item_type"], "ticket")
        with closing(connect(self.store.db_path)) as conn, write_tx(conn):
            rebuild_fts(conn)
        self.assertEqual(len(self.store.search("gear")), 2)
        self.store.patch_ticket(tid, {"tech_notes": "axle"}, version=1)
        self.assertEqual(len(self.store.search("gear")), 1)
        self.assertEqual(self.store.search('NEAR("x", *) OR )'), [])

    def test_validation_conflicts_and_commit_notifications(self):
        tid = self.ticket("A")
        with self.store.subscribe() as event:
            for ops in ([], [None] * 101,
                        [dict(op="ticket_update", ticket_id=tid, fields={"title": ""})],
                        [dict(op="ticket_update", ticket_id=tid, fields={"version": 2})],
                        [dict(op="anchor_add", target_type="ticket", target_id=tid, anchor={"repo": "r", "kind": "commit"})]):
                with self.assertRaises(DomainError):
                    self.store.mutate(ops)
            with self.assertRaisesRegex(DomainError, "revision"):
                self.store.patch_ticket(tid, {"parent_id": None}, version=1)
            self.assertFalse(event.is_set())
            self.store.patch_ticket(tid, {"title": "B"}, version=1)
            self.assertTrue(event.is_set())
            event.clear()
            with self.assertRaises(DomainError) as caught:
                self.store.patch_ticket(tid, {"title": "C"}, version=1)
            self.assertEqual(caught.exception.details["current"]["title"], "B")
            self.assertFalse(event.is_set())

    def test_two_concurrent_writers_cannot_both_win(self):
        tid = self.ticket("A")
        gate = Barrier(2)
        def change(title):
            gate.wait(5)
            try:
                self.store.patch_ticket(tid, {"title": title}, version=1)
                return 200
            except DomainError as exc:
                return exc.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(change, ("B", "C"))), [200, 409])
        self.assertEqual(self.store.revision(), 2)
