from contextlib import closing, redirect_stdout
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import sqlite3
from unittest.mock import patch

from moldloft.git_truth import GitTruth
from moldloft.markdown_io import export_workspace
from moldloft.reducer import NOTE_ID_RE, body_hash
from moldloft.store import Store
from scripts.migrate_legacy import main, migrate_legacy
from tests import StoreCase


DOCK = "d-" + "1" * 32
OLD_DATE = "2025-11-20 09:15:01.000000"


def database(path, version):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript("""
            CREATE TABLE tickets(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, status TEXT, priority TEXT);
            CREATE TABLE ticket_edges(blocker_id INTEGER, blocked_id INTEGER);
            CREATE TABLE code_links(id INTEGER PRIMARY KEY, ticket_id INTEGER, repo TEXT, kind TEXT, ref TEXT,
                                    path TEXT DEFAULT '', start_line INTEGER, end_line INTEGER);
        """)
        if version == 4:
            conn.executescript("""
                ALTER TABLE tickets ADD description TEXT DEFAULT '';
                ALTER TABLE tickets ADD tech_notes TEXT DEFAULT '';
                ALTER TABLE tickets ADD group_name TEXT DEFAULT '';
                ALTER TABLE tickets ADD parent_id INTEGER;
                ALTER TABLE tickets ADD version INTEGER DEFAULT 1;
                CREATE TABLE graph_meta(id INTEGER PRIMARY KEY, revision INTEGER, next_ticket_id INTEGER);
                CREATE TABLE drydocks(id TEXT PRIMARY KEY, name TEXT, created_at TEXT);
                CREATE TABLE note_write_attempts(id TEXT PRIMARY KEY, files_json TEXT);
                CREATE TABLE proposals(id TEXT PRIMARY KEY, status TEXT);
            """)
        conn.execute(f"PRAGMA user_version={version}")


def sql(path, statement, rows):
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executemany(statement, rows)


def note(directory, key, kind, state, *, title=None, body="Original Markdown\n", relations=(), **meta):
    directory.mkdir(parents=True, exist_ok=True)
    fields = dict(id=key, kind=kind, state=state, title=title or key,
                  relations=[dict(type=r, target=t) for r, t in relations], **meta)
    # Exercise the legacy writer's JSON flow arrays plus its hand-edited plain scalars.
    lines = [f"{k}: {v if k in ('kind', 'state', 'title', 'id') else json.dumps(v, ensure_ascii=False)}"
             for k, v in fields.items()]
    path = directory / (key + ".md")
    path.write_bytes(("\ufeff---\n" + "\n".join(lines) + "\n---\n" + body).replace("\n", "\r\n").encode())
    return path


def files(root):
    return {p.relative_to(root): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


def cli(*args):
    out = StringIO()
    with redirect_stdout(out):
        status = main([*map(str, args), "--json"])
    return status, json.loads(out.getvalue())


class LegacyMigrationTests(StoreCase):
    def setUp(self):
        super().setUp()
        environment = patch.dict(os.environ, MOLDLOFT_REPOS="")
        environment.start()
        self.addCleanup(environment.stop)
        self.legacy = self.root / "legacy #% data"
        self.source = self.legacy / "tickets.db"
        self.notes = self.legacy / "notebooks" / "notes"
        self.secondary = self.legacy / "drydocks" / DOCK / "tickets.db"
        self.other_notes = self.notes.parent / "drydocks" / DOCK / "notes"
        self.target = self.root / "output" / "moldloft.db"

    def multi(self):
        for path in (self.source, self.secondary):
            database(path, 4)
            sql(path, "INSERT INTO graph_meta VALUES (1, ?, ?)", [(8, 21)])
        sql(self.source, "INSERT INTO drydocks VALUES (?, ?, ?)",
            [("default", "Release", OLD_DATE), (DOCK, "Experiments", OLD_DATE)])
        sql(self.source, "INSERT INTO tickets(id,title,status,priority) VALUES (?,?,?,?)",
            [(3, "Check callers", "open", "high"), (17, "Publish", "open", "medium")])
        sql(self.secondary, "INSERT INTO tickets(id,title,status,priority,group_name,parent_id,version) VALUES (?,?,?,?,?,?,?)",
            [(3, "Implement", "open", "high", "API", 17, 4), (17, "Prototype", "done", "low", "API", None, 3)])
        for path in (self.source, self.secondary):
            sql(path, "INSERT INTO ticket_edges VALUES (?,?)", [(17, 3)])
        self.notes.mkdir(parents=True)
        self.other_notes.mkdir(parents=True)

    def run_migration(self, **kwargs):
        return migrate_legacy(self.source, self.target, source_notes=self.notes, **kwargs)

    def test_multi_drydock_preview_apply_and_git_ground_truth(self):
        self.multi()
        self.checkout()
        pinned = self.git("rev-parse", "HEAD")
        self.git("branch", "release", pinned)
        self.file.write_bytes(b"alpha\nchanged\ncharlie\ndelta\n")
        head = self.commit("later edit")
        sql(self.secondary, "INSERT INTO code_links VALUES (?,?,?,?,?,?,?,?)", [
            (1, 3, "app", "file", pinned, self.file.name, 2, 2),
            (2, 3, "offline", "file", pinned, self.file.name, 2, 2),
            (3, 3, "app", "commit", "e" * 40, "", None, None),
            (4, 17, "app", "branch", "release", "", None, None),
        ])
        sql(self.source, "INSERT INTO proposals VALUES (?,?)", [("p", "pending"), ("q", "applied")])
        note(self.notes, "n-index", "metadata", "recorded")
        note(self.other_notes, "n-index", "idea", "selected",
             relations=[("option", "note:n-evidence"), ("relates", "ticket:3")])
        # The remapped ID must also avoid a unique original ID processed later.
        note(self.other_notes, "n-index-2", "source", "recorded")
        note(self.other_notes, "n-evidence", "evidence", "recorded", relations=[
            ("supports", "ticket:3"), ("challenges", "ticket:17"), ("derived_from", "note:n-index-2"),
            ("relates", "note:n-index-2"), ("relates", "note:n-index"), ("relates", "ticket:99")])
        note(self.other_notes, "n-premise", "assumption", "proposed", relations=[("assumes", "ticket:3")])
        note(self.notes, "n-result", "decision", "accepted", relations=[("relates", "note:n-index")])
        original = files(self.legacy)
        roots = self.root / "repos.json"
        roots.write_text(json.dumps({"app": str(self.root / "overridden")}), encoding="utf-8")
        args = ("--source-db", self.source, "--source-notes", self.notes, "--target-db", self.target,
                "--repos", roots, "--repo", f"app={self.repo}", "--repo", f"unused={self.repo}")
        status, preview = cli(*args)
        self.assertEqual(status, 0, preview)
        self.assertFalse(preview["applied"])
        self.assertFalse(self.target.parent.exists())
        status, applied = cli(*args, "--apply")
        self.assertEqual(status, 0, applied)
        self.assertEqual(applied, dict(preview, applied=True))
        self.assertEqual({k: applied[k] for k in ("graphs", "tickets", "edges", "notes", "note_links",
                                                  "anchors", "verified_anchors", "unverified_anchors")},
                         dict(graphs=2, tickets=4, edges=2, notes=6, note_links=8, anchors=4,
                              verified_anchors=2, unverified_anchors=2))
        self.assertEqual(applied["ticket_id_map"], {"default": {"3": 3, "17": 17}, DOCK: {"3": 21, "17": 22}})
        renamed = applied["note_id_map"][DOCK]["n-index"]
        self.assertRegex(renamed, NOTE_ID_RE)
        self.assertNotIn(renamed, {"n-index", "n-index-2"})
        warnings = "\n".join(applied["warnings"])
        for message in ("1 pending legacy proposal", "dangling relates target ticket:99", "offline@", "e" * 40):
            self.assertIn(message, warnings)
        store = Store(self.target, GitTruth({"app": self.repo}))
        primary, other = store.snapshot(), store.snapshot(DOCK)
        self.assertEqual((primary["name"], other["name"], other["revision"]), ("Release", "Experiments", 8))
        self.assertEqual(primary["edges"], [dict(blocker_id=17, blocked_id=3)])
        self.assertEqual(other["edges"], [dict(blocker_id=22, blocked_id=21)])
        self.assertEqual((other["tickets"][0]["parent_id"], other["tickets"][0]["version"]), (22, 4))
        self.assertTrue(other["tickets"][0]["ready"])
        self.assertEqual(other["tickets"][0]["review_notes"], ["n-premise"])
        self.assertEqual({(link["source_note_id"], link["relation"], link["target_type"], link["target_id"])
                          for link in other["note_links"]}, {
            (renamed, "relates", "note", "n-evidence"), (renamed, "relates", "ticket", 21),
            ("n-evidence", "supports", "ticket", 21), ("n-evidence", "challenges", "ticket", 22),
            ("n-evidence", "relates", "note", "n-index-2"), ("n-evidence", "relates", "note", renamed),
            ("n-premise", "assumes", "ticket", 21)})
        self.assertEqual(primary["note_links"][0]["target_id"], "n-index")
        anchor, offline, missing, branch = other["anchors"]
        self.assertEqual((anchor["ref"], anchor["pinned_sha"], anchor["target_id"]), (pinned, pinned, 21))
        self.assertEqual((anchor["content_hash"], anchor["excerpt"]), (sha256(b"bravo\n").hexdigest(), "bravo\n"))
        self.assertEqual((branch["ref"], branch["pinned_sha"], branch["target_id"]), ("release", pinned, 22))
        for unverified in (offline, missing):
            self.assertEqual((unverified["content_hash"], unverified["excerpt"], unverified["verified_at"]), ("", "", ""))
        self.assertEqual(offline["ref"], pinned)
        results = store.verify_ground_truth(DOCK)
        self.assertEqual([r["status"] for r in results], ["drifted", "missing", "missing", "fresh"])
        self.assertEqual(results[0]["head_sha"], head)
        self.assertEqual(store.search("Impl", DOCK)[0]["item_id"], "21")
        self.assertEqual(store.search("Original Markdown", "default", "note")[0]["item_type"], "note")
        archive = self.root / "archive"
        self.assertEqual(export_workspace(store, archive)["graphs"], 2)
        self.assertIn(pinned, (archive / "graphs" / DOCK / "anchors.yaml").read_text(encoding="utf-8"))
        store.mutate([dict(op="ticket_create", temp_id="new:t", fields=dict(title="Next"))])
        with closing(sqlite3.connect(self.target)) as conn:
            self.assertEqual(dict(conn.execute("SELECT name,seq FROM sqlite_sequence")), dict(tickets=23, anchors=4))
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(files(self.legacy), original)

    def test_all_states_bilingual_abstracts_and_adoption(self):
        database(self.source, 2)
        directory = self.source.parent / "notes"
        states = {"assumption": ["proposed", "adopted", "refuted", "retired"],
                  "question": ["open", "resolved", "archived"], "evidence": ["recorded", "superseded"],
                  "decision": ["proposed", "accepted", "superseded"], "idea": ["open", "selected", "parked"],
                  "source": ["recorded", "superseded"], "metadata": ["recorded"]}
        title, body, zh, en = "前提: α", "## Why\n正文\n", "假" * 550, "Evidence " * 100
        digest = sha256(json.dumps([title, body], ensure_ascii=False).encode()).hexdigest()
        for kind, values in states.items():
            for state in values:
                note(directory, f"n-{kind}-{state}", kind, state, title=title, body=body,
                     adopted_digest=digest if state == "adopted" else None, abstract_zh=zh, abstract_en=en)
        note(directory, "n-old", "assumption", "adopted", body="changed", adopted_digest=digest,
             abstract_zh="摘要", abstract_en="Summary")
        original = files(self.legacy)
        result = migrate_legacy(self.source, self.target, apply=True)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["warnings"], [])
        snap = Store(self.target).snapshot()
        notes = {n["id"]: n for n in snap["notes"]}
        for kind, values in states.items():
            for state in values:
                with self.subTest(kind=kind, state=state):
                    n = notes[f"n-{kind}-{state}"]
                    expected_kind = "note" if kind in {"idea", "source", "metadata"} else kind
                    expected_state = state
                    if kind == "question" and state == "archived":
                        expected_state = "resolved"
                    if expected_kind == "note":
                        expected_state = "archived" if state in ("parked", "superseded") else "active"
                    self.assertEqual((n["kind"], n["state"], n["in_graph"]), (expected_kind, expected_state, kind != "metadata"))
                    self.assertEqual(n["title"], title)
                    self.assertLessEqual(len(n["summary"]), 1000)
                    self.assertEqual(n["body"], zh + "\n\n" + en + "\n\n" + body)
        fresh, stale = notes["n-assumption-adopted"], notes["n-old"]
        self.assertEqual(fresh["adopted_body_hash"], body_hash(title, fresh["body"]))
        self.assertFalse(fresh["adoption_stale"])
        self.assertEqual((stale["state"], stale["adoption_stale"], stale["adopted_body_hash"]), ("adopted", True, "legacy-stale"))
        self.assertEqual((stale["summary"], stale["body"]), ("摘要\n\nSummary", "changed"))
        self.assertEqual(files(self.legacy), original)

    def test_excluded_legacy_anchor_is_preserved_without_backfilling_an_excerpt(self):
        database(self.source, 1)
        self.checkout()
        sql(self.source, "INSERT INTO tickets VALUES (?,?,?,?)", [(1, "Review", "open", "low")])
        sql(self.source, "INSERT INTO code_links VALUES (?,?,?,?,?,?,?,?)", [
            (1, 1, "app", "file", self.git("rev-parse", "HEAD"), self.file.name, 2, 2)])
        config = self.root / "repos.json"
        config.write_text(json.dumps({"app": {"path": str(self.repo), "exclude": [self.file.name]}}), encoding="utf-8")
        status, report = cli("--source-db", self.source, "--target-db", self.target, "--repos", config, "--apply")
        self.assertEqual(status, 0, report)
        self.assertEqual(report["unverified_anchors"], 1)
        anchor, = Store(self.target).get_item("#1")["anchors"]
        self.assertEqual((anchor["path"], anchor["content_hash"], anchor["excerpt"]), (self.file.name, "", ""))

    def test_single_database_optional_notes_and_replace(self):
        database(self.source, 1)
        sql(self.source, "INSERT INTO tickets VALUES (?,?,?,?)", [(71, "Original", "open", "low")])
        status, result = cli("--source-db", self.source)
        self.assertEqual((status, result["ticket_id_map"]), (0, {"default": {"71": 71}}))
        self.assertEqual(cli("--source-db", self.source, "--apply")[0], 1)
        self.assertTrue(migrate_legacy(self.source, self.target, apply=True)["applied"])
        store = Store(self.target)
        store.create_graph("An existing graph")
        result = migrate_legacy(self.source, self.target, apply=True)
        self.assertIn("--replace", result["errors"][0])
        self.assertEqual(len(store.list_graphs()), 2)
        self.assertTrue(migrate_legacy(self.source, self.target, apply=True, replace=True)["applied"])
        self.assertEqual(len(store.list_graphs()), 1)
        self.assertEqual(store.get_item(71)["item"]["title"], "Original")

    def test_full_abstracts_survive_even_at_legacy_body_limit(self):
        database(self.source, 3)
        body, abstract = "a" * 20000, "z" * 1200
        note(self.notes, "n-limit", "question", "open", body=body, abstract_en=abstract)
        result = self.run_migration(apply=True)
        self.assertTrue(result["applied"], result)
        self.assertIn("20000", result["warnings"][0])
        self.assertEqual(Store(self.target).get_item("n-limit")["item"]["body"], abstract + "\n\n" + body)

    def test_wal_source_with_uncheckpointed_commits_and_no_side_effects(self):
        database(self.source, 3)
        with closing(sqlite3.connect(self.source)) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("INSERT INTO tickets VALUES (12,'WAL only','done','low')")
            writer.commit()
            original = files(self.legacy)
            self.assertIn(Path("tickets.db-wal"), original)
            result = migrate_legacy(self.source, self.target, apply=True)
            self.assertEqual(result["errors"], [])
            self.assertEqual(Store(self.target).get_item(12)["item"]["title"], "WAL only")
            self.assertEqual(files(self.legacy), original)

    def test_invalid_notes_and_secondary_recovery_prevent_any_write(self):
        self.multi()
        bad = self.notes / "n-broken.md"
        bad.write_bytes(b"---\nid: \xff")
        invalid = note(self.notes, "n-invalid", "question", "open", relations=[("assumes", "ticket:3")])
        sql(self.secondary, "INSERT INTO note_write_attempts VALUES (?,?)", [("in-flight", "[]")])
        original = files(self.legacy)
        status, result = cli("--source-db", self.source, "--source-notes", self.notes,
                             "--target-db", self.target, "--apply", "--replace")
        self.assertEqual(status, 1)
        self.assertFalse(result["applied"])
        for path in (bad, invalid):
            self.assertTrue(any(path.name in s for s in result["warnings"]))
            self.assertTrue(any(path.name in s for s in result["errors"]))
        self.assertTrue(any(DOCK in s and "Finish recovery in legacy Moldloft" in s for s in result["errors"]))
        self.assertFalse(self.target.exists())
        self.assertEqual(files(self.legacy), original)

    def test_missing_secondary_or_explicit_notebook_is_an_error(self):
        self.multi()
        self.other_notes.rmdir()
        for missing in (self.other_notes, self.secondary):
            with self.subTest(missing=missing):
                if missing == self.secondary:
                    self.secondary.unlink()
                result = self.run_migration(apply=True)
                self.assertIn(DOCK, result["errors"][0])
                self.assertFalse(self.target.exists())
        result = migrate_legacy(self.source, source_notes=self.root / "absent")
        self.assertTrue(any("absent" in message for message in result["errors"]))

    def test_replace_protects_sources_notes_and_sidecars_including_aliases(self):
        self.multi()
        saved = note(self.notes, "n-private", "metadata", "recorded")
        alias = self.root / "hardlink.db"
        os.link(self.secondary, alias)
        original = files(self.legacy)
        for target in (self.source, self.secondary, saved, alias, Path(str(self.source) + "-wal"), self.notes / "n-new.md"):
            with self.subTest(target=target):
                result = migrate_legacy(self.source, target, source_notes=self.notes, apply=True, replace=True)
                self.assertIn("legacy source", result["errors"][0])
                self.assertFalse(result["applied"])
        self.assertEqual(files(self.legacy), original)

    def test_failed_replace_rolls_back_schema_data_and_fts(self):
        self.multi()
        self.assertTrue(self.run_migration(apply=True)["applied"])
        store = Store(self.target)
        before = store.snapshot(DOCK)
        sql(self.source, "UPDATE drydocks SET name=? WHERE id=?", [("RELEASE", DOCK)])
        failed = self.run_migration(apply=True, replace=True)
        self.assertFalse(failed["applied"])
        self.assertTrue(failed["errors"])
        self.assertEqual(store.snapshot(DOCK), before)
        self.assertEqual(store.search("Impl", DOCK)[0]["item_id"], "21")

    def test_long_colliding_note_ids(self):
        self.multi()
        key = "n-" + "x" * 62
        for directory in (self.notes, self.other_notes):
            note(directory, key, "question", "open")
        preview = self.run_migration()
        new = preview["note_id_map"][DOCK][key]
        self.assertRegex(new, NOTE_ID_RE)
        self.assertLessEqual(len(new), 64)
        self.assertNotEqual(new, key)
        self.assertEqual(self.run_migration()["note_id_map"], preview["note_id_map"])
