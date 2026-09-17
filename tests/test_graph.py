"""Isolated persistence, HTTP, concurrency and model-boundary regressions."""
import concurrent.futures
import json
import sqlite3
import tempfile
import time
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tickets.app import create_app
from tickets.db import initialize
from tickets.models import Ticket
from tickets.service import Store, DomainError
from tickets import copilot


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "tickets.db"
        self.engine = initialize(self.path)
        self.store = Store(self.engine)

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def create(self, title="Task", **fields):
        return self.store.mutate([{"op": "create", "fields": {"title": title, **fields}}])["created"]["new:0"]

    def test_nested_group_and_delete_preserves_children(self):
        a = self.create("Parent", group_name="alpha")
        b = self.create("Child", parent_id=a)
        c = self.create("Grandchild", parent_id=b)
        self.store.mutate([{"op": "update", "ticket_id": a, "fields": {"group_name": "beta"}}])
        self.assertEqual({t["group_name"] for t in self.store.graph()["tickets"]}, {"beta"})
        self.store.mutate([{"op": "delete", "ticket_id": b}])
        self.assertEqual(self.store.graph()["tickets"][-1]["parent_id"], a)
        self.assertEqual(self.create("New"), c + 1)

    def test_cycle_and_rollback(self):
        a, b = self.create("A"), self.create("B")
        before = self.store.graph()
        with self.assertRaises(DomainError) as error:
            self.store.mutate([{"op": "edge_add", "blocker_id": a, "blocked_id": b},
                               {"op": "edge_add", "blocker_id": b, "blocked_id": a}])
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(before, self.store.graph())
        with self.assertRaises(DomainError):
            self.store.mutate([{"op": "update", "ticket_id": a, "fields": {"parent_id": b}},
                               {"op": "update", "ticket_id": b, "fields": {"parent_id": a}}])

    def test_concurrent_edges_cannot_jointly_create_cycle(self):
        a, b = self.create("A"), self.create("B")
        def insert(pair):
            try:
                self.store.mutate([{"op": "edge_add", "blocker_id": pair[0], "blocked_id": pair[1]}])
                return True
            except DomainError:
                return False
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(insert, [(a, b), (b, a)])), [False, True])
        self.assertEqual(len(self.store.graph()["edges"]), 1)

    def test_readiness_depends_on_done_not_closed(self):
        a, b = self.create("A", status="closed"), self.create("B")
        self.store.mutate([{"op": "edge_add", "blocker_id": a, "blocked_id": b}])
        self.assertFalse(self.store.graph()["tickets"][1]["ready"])
        self.store.mutate([{"op": "update", "ticket_id": a, "fields": {"status": "done"}}])
        self.assertTrue(self.store.graph()["tickets"][1]["ready"])

    def test_noop_preserves_revision_with_double_digit_edge_ids_and_preview_readiness(self):
        ids = [self.create() for _ in range(12)]
        self.store.mutate([{'op': 'edge_add', 'blocker_id': i, 'blocked_id': 12} for i in ids[:-1]])
        before = self.store.graph()
        result = self.store.mutate([{'op': 'update', 'ticket_id': 1, 'fields': {'title': 'Task'}}])
        self.assertEqual(result['revision'], before['revision'])
        preview = self.store.preview([{'op': 'update', 'ticket_id': i, 'fields': {'status': 'done'}} for i in ids[:-1]])
        self.assertTrue(preview['preview']['tickets'][-1]['ready'])
        self.assertEqual(self.store.graph(), before)

    def test_stale_revision_and_ticket_version(self):
        a = self.create()
        before = self.store.graph()
        self.store.mutate([{"op": "update", "ticket_id": a, "fields": {"title": "Edited"}}], versions={a: 1})
        with self.assertRaises(DomainError):
            self.store.mutate([{"op": "update", "ticket_id": a, "fields": {"title": "Stale"}}], versions={a: 1})
        with self.assertRaises(DomainError):
            self.store.mutate([{"op": "delete", "ticket_id": a}], revision=before["revision"])

    def test_preview_does_not_write_and_apply_is_idempotent(self):
        a = self.create()
        ops = [{"op": "create", "temp_id": "new:child", "fields": {"title": "Child", "parent_id": a}},
               {"op": "edge_add", "blocker_id": a, "blocked_id": "new:child"}]
        before = self.store.graph()
        preview = self.store.preview(ops)
        self.assertEqual(before, self.store.graph())
        request_id = str(uuid.uuid4())
        result = self.store.mutate(ops, revision=preview["base_revision"], request_id=request_id)
        self.assertEqual(result, self.store.mutate(ops, revision=preview["base_revision"], request_id=request_id))
        self.assertEqual(len(self.store.graph()["tickets"]), 2)

    def test_translation_is_versioned_and_failure_does_not_retry(self):
        a = self.create()
        with Session(self.engine) as session:
            session.get(Ticket, a).translation_due = 0
            session.commit()
        def outdated(title, description):
            self.store.mutate([{"op": "update", "ticket_id": a, "fields": {"title": "New source"}}])
            return ("old translation", "")
        self.store.translate_one(outdated)
        self.assertEqual(self.store.graph()["tickets"][0]["title_zh"], "")
        with Session(self.engine) as session:
            session.get(Ticket, a).translation_due = 0
            session.commit()
        translator = Mock(side_effect=ValueError())
        self.store.translate_one(translator)
        self.assertFalse(self.store.translate_one(translator))
        self.assertEqual(translator.call_count, 1)
        self.assertTrue(self.store.graph()["tickets"][0]["translation_error"])

    def test_deleting_ticket_removes_edges(self):
        a, b = self.create(), self.create()
        self.store.mutate([{"op": "edge_add", "blocker_id": a, "blocked_id": b}])
        self.store.mutate([{"op": "delete", "ticket_id": a}])
        self.assertEqual(self.store.graph()["edges"], [])

    def test_stale_proposal_and_invalid_batch_leave_no_partial_changes(self):
        a = self.create()
        operations = [{"op": "update", "ticket_id": a, "fields": {"title": "Proposed"}}]
        preview = self.store.preview(operations)
        self.create('Unrelated edit')
        before = self.store.graph()
        with self.assertRaises(DomainError):
            self.store.mutate(operations, revision=preview['base_revision'])
        with self.assertRaises(DomainError):
            self.store.mutate([*operations, {"op": "edge_add", "blocker_id": a, "blocked_id": a}])
        self.assertEqual(before, self.store.graph())

    def test_interrupted_or_invalid_model_response_cannot_produce_preview(self):
        a = self.create()
        prepared = copilot.prepare(self.store, [{'role': 'user', 'content': 'Edit fixture'}], a, [])
        for events in [
            [{'type': 'tool', 'name': 'propose_ticket_changes', 'arguments': {'operations': [
                {'op': 'update', 'ticket_id': a, 'fields': {'title': 'Incomplete'}}]}}],
            [{'type': 'tool', 'name': 'run_shell', 'arguments': {}}, {'type': 'done'}],
            [{'type': 'tool', 'name': 'propose_ticket_changes', 'arguments': {'operations': [
                {'op': 'update', 'ticket_id': a, 'fields': {'version': 123}}]}}, {'type': 'done'}],
        ]:
            output = list(copilot.events(self.store, prepared, streamer=lambda **_: iter(events)))
            self.assertTrue(any(e['type'] == 'error' for e in output))
            self.assertFalse(any(e['type'] == 'proposal' for e in output))
        self.assertEqual(self.store.graph(), prepared[0])


class MigrationTests(unittest.TestCase):
    def test_legacy_database_backup_and_idempotence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tickets.db"
            with closing(sqlite3.connect(path)) as db:
                db.execute("CREATE TABLE tickets (id INTEGER PRIMARY KEY, title TEXT, description TEXT, status TEXT, priority TEXT, created_at DATETIME, updated_at DATETIME)")
                db.execute("INSERT INTO tickets VALUES (42, 'Legacy', 'original', 'open', 'medium', '2026-01-01', '2026-01-01')")
                db.commit()
            engine = initialize(path)
            self.assertEqual(Store(engine).graph()["tickets"][0]["description"], "original")
            engine.dispose()
            engine = initialize(path)
            engine.dispose()
            backups = list((path.parent / "backups").glob("*.sqlite3"))
            self.assertEqual(len(backups), 1)
            with closing(sqlite3.connect(backups[0])) as db:
                self.assertEqual(db.execute("SELECT title FROM tickets").fetchone()[0], "Legacy")
                self.assertNotIn("version", {r[1] for r in db.execute("PRAGMA table_info(tickets)")})


class APITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.called = []
        def model(**kwargs):
            self.called.append(kwargs)
            yield {"type": "text", "text": "Preview only"}
            yield {"type": "tool", "name": "propose_ticket_changes", "arguments": {"operations": [
                {"op": "update", "ticket_id": 1, "fields": {"title": "Proposed title"}}]}}
            yield {"type": "done"}
        self.app = create_app(Path(self.tmp.name) / "test.db", translator=None, repo_roots={}, copilot_streamer=model)
        self.client = TestClient(self.app).__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def create(self, title="Original"):
        revision = self.client.get('/api/graph').json()['revision']
        response = self.client.post('/api/tickets', json={"fields": {"title": title}, "revision": revision, "request_id": str(uuid.uuid4())})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['created']['new:ticket']

    def test_pages_and_forms_share_graph(self):
        a = self.create()
        for page in ('/', '/graph', f'/tickets/{a}'):
            response = self.client.get(page)
            self.assertEqual(response.status_code, 200, response.text)
        graph = self.client.get('/api/graph').json()
        response = self.client.post(f'/tickets/{a}', data={"title": "From form", "version": 1,
            "revision": graph['revision'], "request_id": str(uuid.uuid4())}, follow_redirects=False)
        self.assertEqual(response.status_code, 303, response.text)
        self.assertEqual(self.client.get('/api/graph').json()['tickets'][0]['title'], 'From form')

    def test_conflict_preserves_server_and_returns_current(self):
        a = self.create()
        body = {"fields": {"title": "One"}, "version": 1, "request_id": str(uuid.uuid4())}
        self.assertEqual(self.client.patch(f'/api/tickets/{a}', json=body).status_code, 200)
        body.update(fields={"title": "Two"}, request_id=str(uuid.uuid4()))
        conflict = self.client.patch(f'/api/tickets/{a}', json=body)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()['current']['title'], 'One')

    def test_copilot_preview_scope_and_no_transcript_tables(self):
        self.create()
        self.create('Not attached')
        before = self.client.get('/api/graph').json()
        response = self.client.post('/api/copilot/chat', json={"messages": [{"role": "user", "content": "Rename this"}], "selected_id": 1})
        self.assertEqual(response.status_code, 200)
        events = [json.loads(part[6:]) for part in response.text.strip().split('\n\n')]
        proposal = next(e for e in events if e['type'] == 'proposal')
        self.assertEqual(self.client.get('/api/graph').json(), before)
        self.assertNotIn('Not attached', json.dumps(self.called[0]['messages']))
        applied = self.client.post('/api/copilot/apply', json={"operations": proposal['operations'],
            "revision": proposal['base_revision'], "context_ids": proposal['context_ids'], "request_id": str(uuid.uuid4())})
        self.assertEqual(applied.status_code, 200, applied.text)
        with closing(sqlite3.connect(Path(self.tmp.name) / 'test.db')) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertFalse(any('chat' in t or 'transcript' in t for t in tables))
            self.assertEqual(db.execute('SELECT count(*) FROM proposals').fetchone()[0], 0,
                             'The synchronous Copilot path still stores no pending proposal or rationale')

    def test_cross_origin_write_rejected(self):
        response = self.client.post('/api/tickets', headers={'Origin': 'https://example.invalid'}, json={})
        self.assertEqual(response.status_code, 403)

    def test_disabled_translation_is_visible_and_cannot_claim_a_retry(self):
        a = self.create()
        self.assertIn('data-translation-enabled="false"', self.client.get('/graph').text)
        before = self.client.get('/api/graph').json()
        self.assertEqual(self.client.post(f'/api/tickets/{a}/translation').status_code, 503)
        self.assertEqual(self.client.get('/api/graph').json(), before)

    def test_out_of_context_proposal_rejected(self):
        self.create(); self.create('Elsewhere')
        with self.assertRaises(DomainError):
            copilot.validate_scope([{"op": "update", "ticket_id": 2, "fields": {"title": "Bad"}}], {1}, self.app.state.store.graph())


if __name__ == '__main__':
    unittest.main()
