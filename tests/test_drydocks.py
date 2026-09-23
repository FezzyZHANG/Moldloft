"""Drydock isolation, legacy upgrade and unavailable-store behavior on temporary data."""
from concurrent.futures import ThreadPoolExecutor
import contextlib
import io
from pathlib import Path
import sqlite3
import tempfile
import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tickets import basis
from tickets.app import create_app
from tickets.db import initialize
from tickets.drydocks import Drydocks
from tickets.models import NoteWriteAttempt
from tickets.maintenance import main as maintenance
from tickets.notes import Notebook
from tickets.service import Store, DomainError


class DrydockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.database = self.root / 'db' / 'tickets.db'
        self.notebook = self.root / 'authored' / 'notes'
        self.app = create_app(self.database, notes_path=self.notebook, translator=None, repo_roots={})
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.docks = self.app.state.drydocks

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def create(self, name='Research'):
        response = self.client.post('/api/drydocks', json={'name': name, 'request_id': str(uuid.uuid4())})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['id']

    def ticket(self, dock, title='Fixture', request_id=None):
        graph = self.client.get('/api/graph', params={'drydock': dock}).json()
        response = self.client.post('/api/tickets', params={'drydock': dock}, json={
            'fields': {'title': title}, 'revision': graph['revision'], 'request_id': request_id or str(uuid.uuid4())})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_provenance_target_kinds_are_checked_within_the_selected_drydock(self):
        dock = self.create()
        for ident, kind, state in [('default', 'source', 'recorded'), (dock, 'question', 'open')]:
            response = self.client.put('/api/notes/n-source', params={'drydock': ident}, json={
                'fields': {'title': 'Same local ID', 'kind': kind, 'state': state}})
            self.assertEqual(response.status_code, 200, response.text)
        evidence = {'fields': {'title': 'Finding', 'kind': 'evidence', 'state': 'recorded',
            'relations': [{'type': 'derived_from', 'target': 'note:n-source'}]}}
        self.assertEqual(self.client.put('/api/notes/n-evidence', json=evidence).status_code, 200)
        rejected = self.client.put('/api/notes/n-evidence', params={'drydock': dock}, json=evidence)
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertIn('source note', rejected.json()['message'])
        research = self.client.get('/api/graph', params={'drydock': dock}).json()
        self.assertEqual([n['id'] for n in research['notes']], ['n-source'])
        default = self.client.get('/api/graph').json()
        self.assertEqual(default['relations'][0]['type'], 'derived_from')

    def test_same_ids_and_retry_keys_are_isolated_with_independent_notebooks(self):
        dock = self.create()
        request_id = str(uuid.uuid4())
        self.ticket('default', 'Default ticket', request_id)
        self.ticket(dock, 'Research ticket', request_id)
        for ident in ('default', dock):
            response = self.client.put('/api/notes/n-premise', params={'drydock': ident}, json={
                'fields': {'title': ident, 'body': 'Exact text', 'relations': [{'type': 'relates', 'target': 'ticket:1'}]}})
            self.assertEqual(response.status_code, 200, response.text)
        default = self.client.get('/api/graph').json()
        research = self.client.get('/api/graph', params={'drydock': dock}).json()
        self.assertEqual(default['tickets'][0]['id'], research['tickets'][0]['id'])
        self.assertEqual(default['tickets'][0]['title'], 'Default ticket')
        self.assertEqual(research['tickets'][0]['title'], 'Research ticket')
        self.assertEqual(research['drydock_id'], dock)
        self.assertNotEqual(default['notes_revision'], research['notes_revision'])
        db, notes = self.docks.paths(dock)
        self.assertEqual(db, self.database.parent / 'drydocks' / dock / 'tickets.db')
        self.assertEqual(notes, self.notebook.parent / 'drydocks' / dock / 'notes')
        self.assertTrue((notes / 'n-premise.md').is_file())
        with DrydockRestart(self.database, self.notebook) as restarted:
            self.assertEqual(restarted.get(dock).store.graph(), research)

    def test_metadata_ids_versions_and_navigation_stay_in_their_own_drydock(self):
        dock = self.create()
        for ident in ('default', dock):
            self.ticket(ident, ident)
            response = self.client.put('/api/notes/n-index', params={'drydock': ident}, json={
                'fields': {'title': ident, 'kind': 'metadata', 'state': 'recorded',
                           'body': f'Overview for {ident}', 'relations': [{'type': 'relates', 'target': 'ticket:1'}]}})
            self.assertEqual(response.status_code, 200, response.text)
        original = self.client.get('/api/graph').json()
        other = self.client.get('/api/graph', params={'drydock': dock}).json()
        self.assertEqual(original['notes'][0]['id'], other['notes'][0]['id'])
        self.assertNotEqual(original['notes'][0]['version'], other['notes'][0]['version'])
        self.assertEqual(other['relations'], [])
        self.assertEqual(other['metadata_relations'][0]['target'], 'ticket:1')
        captured = basis.capture(original, note_ids=['n-index'])
        operations = [{'op': 'note_update', 'note_id': 'n-index', 'fields': {'body': 'Wrong scope'}}]
        with self.assertRaisesRegex(DomainError, 'another Drydock'):
            basis.check(captured, other, operations)
        note = other['notes'][0]
        response = self.client.put('/api/notes/n-index', params={'drydock': dock}, json={
            'fields': {'body': 'Changed only here'}, 'version': note['version']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.get('/api/graph').json(), original)
        with DrydockRestart(self.database, self.notebook) as restarted:
            self.assertEqual(restarted.get(dock).notes.read('n-index')['body'], 'Changed only here')
            self.assertEqual(restarted.get().store.graph(), original)

    def test_catalog_create_retries_concurrency_rename_and_name_validation(self):
        request_id = str(uuid.uuid4())
        with ThreadPoolExecutor(max_workers=3) as workers:
            rows = list(workers.map(lambda _: self.docks.create(' Design ', request_id), range(3)))
        self.assertEqual(len({r['id'] for r in rows}), 1)
        original = rows[0]
        self.assertEqual(original['name'], 'Design')
        renamed = self.docks.rename(original['id'], 'Delivery', 1)
        self.assertEqual(renamed['version'], 2)
        self.assertEqual(self.docks.rename(original['id'], 'Delivery', 1), renamed)
        self.assertEqual(self.docks.create('Design', request_id), renamed)
        for name, key in [('Different retry', request_id), ('delivery', str(uuid.uuid4()))]:
            with self.assertRaises(DomainError) as caught:
                self.docks.create(name, key)
            self.assertEqual(caught.exception.status, 409)
        with self.assertRaises(DomainError):
            self.docks.rename(original['id'], 'Stale edit', 1)
        for name in (' ', 'x' * 81, 'two\nlines', 'two\u2028lines'):
            with self.assertRaises(DomainError):
                self.docks.create(name, str(uuid.uuid4()))
        self.docks.rename('default', 'Original', 1)
        self.assertEqual(self.client.get('/api/graph').json()['drydock_id'], 'default')
        self.assertEqual(self.docks.list()['default_id'], 'default')

    def test_cross_dock_basis_is_rejected_even_when_graphs_are_identical(self):
        dock = self.create()
        self.ticket('default')
        self.ticket(dock)
        default, other = self.docks.get(), self.docks.get(dock)
        captured = basis.capture(default.store.graph(), [1])
        operations = [{'op': 'update', 'ticket_id': 1, 'fields': {'title': 'Reviewed'}}]
        for source_basis in (captured, {k: v for k, v in captured.items() if k != 'drydock_id'}):
            with self.assertRaisesRegex(DomainError, 'another Drydock'):
                other.proposals.submit(operations, source_basis, 'Fixture', 'Review fixture', 'free', str(uuid.uuid4()))
        proposal = default.proposals.submit(operations, {k: v for k, v in captured.items() if k != 'drydock_id'},
                                           'Legacy', 'Legacy basis survives', 'free', str(uuid.uuid4()))
        self.ticket(dock, 'Independent change')
        self.assertEqual(default.proposals.apply(proposal['id'])['status'], 'applied')
        self.assertEqual(other.store.graph()['tickets'][0]['title'], 'Fixture')
        self.assertFalse(other.proposals.list()['items'])
        self.assertEqual(self.client.get('/api/proposals/' + proposal['id'], params={'drydock': dock}).status_code, 404)

    def test_copilot_apply_requires_matching_drydock(self):
        dock = self.create()
        self.ticket(dock)
        body = {'operations': [{'op': 'update', 'ticket_id': 1, 'fields': {'title': 'Reviewed'}}],
                'revision': 1, 'request_id': str(uuid.uuid4()), 'context_ids': [1]}
        self.assertEqual(self.client.post('/api/copilot/apply', params={'drydock': dock}, json=body).status_code, 409)
        body['drydock_id'] = dock
        self.assertEqual(self.client.post('/api/copilot/apply', params={'drydock': dock}, json=body).status_code, 200)
        self.assertFalse(self.app.state.store.graph()['tickets'])

    def test_mixed_review_and_maintenance_target_only_the_named_dock(self):
        dock = self.create()
        self.ticket(dock)
        context = self.docks.get(dock)
        context.notes.write('n-premise', {'title': 'Before'}, None, {1})
        captured = basis.capture(context.store.graph(), [1], ['n-premise'])
        proposal = context.proposals.submit([
            {'op': 'update', 'ticket_id': 1, 'fields': {'title': 'After'}},
            {'op': 'note_update', 'note_id': 'n-premise', 'fields': {'body': 'Reviewed together'}}],
            captured, 'Mixed review', 'A bounded fixture', 'free', str(uuid.uuid4()))
        response = self.client.post('/api/proposals/' + proposal['id'] + '/apply', params={'drydock': dock}, json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(context.notes.read('n-premise')['body'], 'Reviewed together')
        self.assertFalse(self.app.state.notes.read('n-premise'))
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = maintenance(['proposals', 'clear', '--status', 'applied', '--apply',
                '--db', str(self.database), '--notes', str(self.notebook), '--drydock', dock])
        self.assertEqual(code, 0, output.getvalue())
        self.assertIn(dock, output.getvalue())
        self.assertFalse(context.proposals.list('applied')['items'])
        self.assertFalse(self.app.state.store.graph()['tickets'])

    def test_unknown_duplicate_and_missing_store_never_fall_back(self):
        unknown = 'd-' + '0' * 32
        self.assertEqual(self.client.get('/api/graph', params={'drydock': unknown}).status_code, 404)
        self.assertEqual(self.client.get('/api/graph?drydock=default&drydock=default').status_code, 422)
        self.assertFalse((self.database.parent / 'drydocks').exists())
        dock = self.create()
        database, notes = self.docks.paths(dock)
        database.unlink()
        self.assertEqual(self.client.get('/api/graph', params={'drydock': dock}).status_code, 503)
        self.assertFalse(database.exists())
        self.assertEqual(self.client.get('/api/graph').status_code, 200)
        other = self.create('Notebook absent')
        _, notebook = self.docks.paths(other)
        notebook.rmdir()
        self.assertEqual(self.client.get('/api/graph', params={'drydock': other}).status_code, 503)
        self.assertFalse(notebook.exists())
        invalid = self.create('Database invalid')
        broken, _ = self.docks.paths(invalid)
        broken.write_bytes(b'')
        self.assertEqual(self.client.get('/api/graph', params={'drydock': invalid}).status_code, 503)
        self.assertEqual(broken.read_bytes(), b'')
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = maintenance(['recover', '--check', '--db', str(self.database),
                                '--notes', str(self.notebook), '--drydock', invalid])
        self.assertEqual(code, 1, output.getvalue())
        self.assertEqual(broken.read_bytes(), b'')

    def test_scoped_form_redirect_and_recovery_failure_leave_siblings_usable(self):
        dock = self.create()
        response = self.client.post('/tickets', params={'drydock': dock}, data={'title': 'Form ticket'}, follow_redirects=False)
        self.assertEqual(response.headers['location'], '/tickets/1?drydock=' + dock)
        self.assertEqual(self.client.get(response.headers['location']).status_code, 200)
        self.assertFalse(self.client.get('/api/graph').json()['tickets'])
        broken = self.create('Recovery blocked')
        database, _ = self.docks.paths(broken)
        engine = initialize(database)
        with Session(engine) as session:
            session.add(NoteWriteAttempt(id=str(uuid.uuid4()), files_json='not valid recovery'))
            session.commit()
        engine.dispose()
        self.assertTrue(self.docks.get(broken).recovery_error)
        blocked = self.client.post('/api/tickets', params={'drydock': broken}, json={
            'fields': {'title': 'Cannot write'}, 'revision': 0, 'request_id': str(uuid.uuid4())})
        self.assertEqual(blocked.status_code, 503)
        self.ticket(dock, 'Sibling remains writable')
        self.assertIsNone(self.docks.get(dock).recovery_error)


class DrydockRestart(contextlib.AbstractContextManager):
    def __init__(self, db, notes):
        self.docks = Drydocks(db, notes)

    def __enter__(self):
        return self.docks

    def __exit__(self, *args):
        self.docks.close()


class UpgradeTests(unittest.TestCase):
    def test_v3_upgrade_preserves_default_rows_note_bytes_and_old_basis(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database, notes = root / 'tickets.db', root / 'notes'
            engine = initialize(database)
            book = Notebook(notes, engine)
            store = Store(engine, notes=book)
            store.mutate([{'op': 'create', 'fields': {'title': 'Legacy ticket'}}])
            book.write('n-legacy', {'title': 'Legacy note', 'body': 'Exact bytes\n'}, None, {1})
            raw = book.path('n-legacy').read_bytes()
            before = store.graph()
            with engine.begin() as connection:
                connection.exec_driver_sql('DROP TABLE drydocks')
                connection.exec_driver_sql('PRAGMA user_version=3')
            engine.dispose()
            with DrydockRestart(database, notes) as docks:
                self.assertEqual(docks.paths('default'), (database, notes))
                self.assertEqual(docks.get().store.graph(), before)
                self.assertEqual(book.path('n-legacy').read_bytes(), raw)
                self.assertEqual(len(docks.list()['items']), 1)
                backups = list((root / 'backups').glob('*before-v4*.sqlite3'))
                self.assertEqual(len(backups), 1)
                with contextlib.closing(sqlite3.connect(backups[0])) as old:
                    self.assertEqual(old.execute('PRAGMA user_version').fetchone()[0], 3)


if __name__ == '__main__':
    unittest.main()
