"""Mixed apply and crash recovery using disposable stores and worker processes (#51).

Retains and extends the withdrawn 228f105 fault fixtures for the current review contract.
Workers run outside the checkout; package discovery follows the imported package location.
"""
import base64
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from sqlalchemy import select

import tickets
from tickets import basis, notes, writes
from tickets.db import initialize
from tickets.models import NoteWriteAttempt
from tickets.notes import Notebook
from tickets.proposals import Proposals, coherent_graph
from tickets.service import Store, DomainError


class MixedApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.open()
        self.store.mutate([{'op': 'create', 'fields': {'title': 'Existing'}}])
        self.book.write('n-existing', {'title': 'Original', 'body': 'Exact original\n'}, None, {1})
        self.path = self.book.path('n-existing')
        self.path.write_bytes(b'\xef\xbb\xbf' + self.path.read_bytes().replace(b'\n', b'\r\n'))
        self.raw = self.path.read_bytes()

    def open(self):
        self.engine = initialize(self.root / 'tickets.db')
        self.book = Notebook(self.root / 'notes', self.engine)
        self.store = Store(self.engine, notes=self.book)
        self.p = Proposals(self.store)

    def restart(self):
        self.engine.dispose()
        self.open()

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def submit(self, ops, origin='free'):
        return self.p.submit(ops, basis.capture(coherent_graph(self.store), [1], ['n-existing']),
            'Mixed fixture', 'Create work and record its provisional premise.', origin, str(uuid.uuid4()))

    def mixed(self):
        return self.submit([
            {'op': 'create', 'temp_id': 'new:work', 'fields': {'title': 'Created once'}},
            {'op': 'note_update', 'note_id': 'n-existing', 'fields': {'body': 'Updated in proposal',
                'relations': [{'type': 'relates', 'target': 'ticket:new:work'}]}},
            {'op': 'note_create', 'temp_id': 'new:premise', 'path': 'notes/n-premise.md', 'fields': {
                'title': 'Premise', 'kind': 'assumption', 'state': 'proposed',
                'relations': [{'type': 'assumes', 'target': 'ticket:new:work'}]}}])

    def attempts(self):
        with self.engine.connect() as connection:
            return [json.loads(raw) for raw in connection.execute(select(NoteWriteAttempt.files_json)).scalars()]

    def assert_restored(self, proposal):
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertFalse(self.book.path('n-premise').exists())
        self.assertEqual(len(self.store.graph()['tickets']), 1)
        self.assertEqual(self.p.get(proposal['id'])['status'], 'pending')
        self.assertFalse(self.attempts())

    def worker(self, proposal, stop_at, recovery=False):
        script = '''
import os, sys
from pathlib import Path
from tickets.db import initialize
from tickets.notes import Notebook
from tickets.service import Store
from tickets.proposals import Proposals
from tickets import notes, writes
root = Path(sys.argv[1])
engine = initialize(root / 'tickets.db')
book = Notebook(root / 'notes', engine)
def stop(name):
    if name == sys.argv[3]:
        os._exit(29)
notes.fault_point = stop
writes.fault_point = stop
if sys.argv[4] == 'recovery':
    writes.recover_pending(engine, book.root)
else:
    Proposals(Store(engine, notes=book)).apply(sys.argv[2])
'''
        env = dict(os.environ)
        env['PYTHONPATH'] = os.pathsep.join(filter(None, [
            str(Path(tickets.__file__).resolve().parent.parent), env.get('PYTHONPATH')]))
        result = subprocess.run([sys.executable, '-B', '-c', script, str(self.root), proposal['id'], stop_at,
                                 'recovery' if recovery else 'apply'], cwd=self.root, env=env,
                                timeout=20, capture_output=True)
        self.assertEqual(result.returncode, 29, result.stderr.decode(errors='replace'))

    def test_mixed_replay_has_one_outcome_and_canonical_relations(self):
        p = self.mixed()
        replay = lambda: self.p.submit(p['operations'], p['basis'], p['summary'], p['rationale'],
                                      p['origin'], p['request_id'])
        self.assertEqual(replay()['id'], p['id'])
        result = self.p.apply(p['id'])
        self.restart()
        with patch.object(self.book, 'write_in_transaction', side_effect=AssertionError('duplicate write')):
            self.assertEqual(self.p.apply(p['id']), result)
            self.assertEqual(replay()['id'], p['id'])
        self.assertEqual(len(self.store.graph()['tickets']), 2)
        self.assertEqual(result['result']['created'], {'new:work': 2})
        for note_id in ('n-existing', 'n-premise'):
            self.assertEqual(self.book.read(note_id)['relations'][0]['target'], 'ticket:2')
        self.assertEqual(self.book.read('n-premise')['state'], 'proposed')
        self.assertFalse(self.attempts())

    def test_forward_aliases_and_final_ticket_allocation_order(self):
        p = self.submit([
            {'op': 'note_create', 'temp_id': 'new:premise', 'path': 'notes/n-a.md', 'fields': {
                'title': 'Premise', 'kind': 'assumption', 'state': 'proposed', 'relations': [
                    {'type': 'assumes', 'target': 'ticket:new:child'},
                    {'type': 'relates', 'target': 'note:new:choice'}]}},
            {'op': 'create', 'temp_id': 'new:discard', 'fields': {'title': 'Discard'}},
            {'op': 'delete', 'ticket_id': 'new:discard'},
            {'op': 'create', 'temp_id': 'new:parent', 'fields': {'title': 'Parent'}},
            {'op': 'create', 'temp_id': 'new:child', 'fields': {'title': 'Child', 'parent_id': 'new:parent'}},
            {'op': 'edge_add', 'blocker_id': 'new:parent', 'blocked_id': 'new:child'},
            {'op': 'note_create', 'temp_id': 'new:choice', 'path': 'notes/n-b.md', 'fields': {
                'title': 'Choice', 'kind': 'decision', 'state': 'proposed'}}])
        result = self.p.apply(p['id'])['result']
        self.assertEqual(result['created'], {'new:parent': 2, 'new:child': 3})
        self.assertEqual(self.book.read('n-a')['relations'], [
            {'type': 'assumes', 'target': 'ticket:3'}, {'type': 'relates', 'target': 'note:n-b'}])
        self.assertEqual(self.store.graph()['tickets'][-1]['parent_id'], 2)

    def test_partial_replace_and_commit_failures_restore_exact_bytes(self):
        for point in ('note_replaced:n-existing', 'note_replaced:n-premise', 'commit'):
            with self.subTest(point=point):
                p = self.mixed()
                def stop(name):
                    if name == point:
                        raise OSError('Injected replacement failure')
                target = patch.object(writes, '_commit', side_effect=OSError('COMMIT failed')) if point == 'commit' else patch.object(notes, 'fault_point', side_effect=stop)
                with target, self.assertRaises((OSError, DomainError)):
                    self.p.apply(p['id'])
                self.assert_restored(p)

    def test_commit_succeeded_but_acknowledgement_failed_retains_data(self):
        p = self.mixed()
        def uncertain(connection):
            connection.commit()
            raise OSError('Lost commit acknowledgement')
        with patch.object(writes, '_commit', side_effect=uncertain), self.assertRaises(OSError):
            self.p.apply(p['id'])
        self.assertFalse(self.attempts())
        self.assertEqual(self.p.apply(p['id'])['status'], 'applied')
        self.assertEqual(len(self.store.graph()['tickets']), 2)
        self.assertEqual(self.book.read('n-premise')['relations'][0]['target'], 'ticket:2')

    def test_crash_after_first_and_last_replace_restores_before_id_reuse(self):
        for point in ('note_replaced:n-existing', 'note_replaced:n-premise', 'before_commit'):
            with self.subTest(point=point):
                p = self.mixed()
                self.worker(p, point)
                self.restart()
                record = self.attempts()[0]
                self.assertEqual(record['context'], {'proposal_id': p['id'], 'assigned_ids': {'new:work': 2}})
                images = {Path(f['path']).stem: f for f in record['files']}
                self.assertEqual(base64.b64decode(images['n-existing']['before']), self.raw)
                self.assertIsNone(images['n-premise']['before'])
                self.assertIn(b'ticket:2', base64.b64decode(images['n-premise']['after']))
                with self.assertRaisesRegex(DomainError, 'ordinary writes are blocked'):
                    self.store.mutate([{'op': 'create', 'fields': {'title': 'Must wait'}}])
                with self.assertRaisesRegex(DomainError, 'Recovery is unresolved'):
                    coherent_graph(self.store)
                writes.recover_pending(self.engine, self.book.root, check_only=True)
                self.assertTrue(self.attempts())
                writes.recover_pending(self.engine, self.book.root)
                self.assert_restored(p)
                self.assertFalse(writes.recover_pending(self.engine, self.book.root)['attempts'])
        # The allocator may reuse a rolled-back ID only after all its relations are gone.
        result = self.store.mutate([{'op': 'create', 'fields': {'title': 'Unrelated'}}])
        self.assertEqual(list(result['created'].values()), [2])
        self.assertFalse(self.book.path('n-premise').exists())
        self.assertEqual(self.book.read('n-existing')['relations'], [])

    def test_crash_after_commit_retries_without_writes_and_repairs_review_outcome(self):
        p = self.mixed()
        self.worker(p, 'after_commit')
        self.restart()
        self.assertFalse(writes.recover_pending(self.engine, self.book.root)['attempts'])
        with patch.object(self.book, 'write_in_transaction', side_effect=AssertionError('duplicate write')):
            result = self.p.apply(p['id'])
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(result['result']['created'], {'new:work': 2})
        self.assertEqual(len(self.store.graph()['tickets']), 2)
        self.assertIn('- status: applied', self.p.files.path(result).read_text(encoding='utf-8'))

    def test_recovery_failure_restart_and_external_conflict_preserve_evidence(self):
        p = self.mixed()
        self.worker(p, 'before_commit')
        with patch.object(writes, 'replace_bytes', side_effect=OSError('Recovery failed')):
            with self.assertRaisesRegex(DomainError, 'remain blocked'):
                writes.recover_pending(self.engine, self.book.root)
        self.assertTrue(self.attempts())
        self.worker(p, 'recovery_file_0', recovery=True)
        self.restart()
        external = b'External content that recovery must preserve'
        self.path.write_bytes(external)
        with self.assertRaisesRegex(DomainError, 'conflicts'):
            writes.recover_pending(self.engine, self.book.root)
        self.assertEqual(self.path.read_bytes(), external)
        self.assertTrue(self.attempts())
        with self.assertRaisesRegex(DomainError, 'ordinary writes are blocked'):
            self.book.write('n-other', {'title': 'Blocked'}, None, {1})
        self.path.write_bytes(self.raw)  # Explicit fixture conflict repair; no force-recovery path.
        writes.recover_pending(self.engine, self.book.root)
        self.assert_restored(p)
        self.assertEqual(self.p.apply(p['id'])['status'], 'applied')

    def test_startup_recovers_before_enabling_writes(self):
        from fastapi.testclient import TestClient
        from tickets.app import create_app
        p = self.mixed()
        self.worker(p, 'before_commit')
        app = create_app(self.root / 'tickets.db', notes_path=self.book.root, translator=None, repo_roots={})
        with TestClient(app):
            self.assertIsNone(app.state.recovery_error)
            self.assert_restored(p)
            self.assertEqual(app.state.proposals.apply(p['id'])['status'], 'applied')

    def test_recovery_refuses_relocated_paths_until_old_root_is_recovered(self):
        p = self.mixed()
        self.worker(p, 'before_commit')
        with self.assertRaisesRegex(DomainError, 'unexpected notebook path'):
            writes.recover_pending(self.engine, self.root / 'different-notebook')
        self.assertTrue(self.attempts())
        writes.recover_pending(self.engine, self.book.root)
        self.assert_restored(p)

    def test_compensation_excludes_a_concurrent_acknowledged_note_save(self):
        p = self.mixed()
        version = self.book.read('n-existing')['version']
        entered, release, started = threading.Event(), threading.Event(), threading.Event()
        original = writes.compensate
        def paused(files):
            entered.set()
            self.assertTrue(release.wait(3))
            original(files)
        def failed():
            with self.assertRaises(OSError):
                self.p.apply(p['id'])
        def saved():
            started.set()
            return self.book.write('n-existing', {'body': 'Acknowledged next save'}, version, {1})
        real_commit = writes._commit
        def fail_proposal_commit(connection):
            if len(connection.info['note_write_scope']['files']) == 2:
                raise OSError('Mixed commit failure')
            real_commit(connection)
        with concurrent.futures.ThreadPoolExecutor(2) as pool, patch.object(writes, 'compensate', side_effect=paused), patch.object(writes, '_commit', side_effect=fail_proposal_commit):
            first = pool.submit(failed)
            self.assertTrue(entered.wait(3))
            second = pool.submit(saved)
            self.assertTrue(started.wait(3))
            try:
                with self.assertRaises(concurrent.futures.TimeoutError):
                    second.result(timeout=.15)
            finally:
                release.set()
            first.result(timeout=3)
            self.assertEqual(second.result(timeout=3)['body'], 'Acknowledged next save')
        self.assertEqual(self.book.read('n-existing')['body'], 'Acknowledged next save')
        self.assertFalse(self.book.path('n-premise').exists())
        self.assertEqual(len(self.store.graph()['tickets']), 1)

    def test_simultaneous_approvals_return_the_same_committed_outcome(self):
        p = self.mixed()
        start = threading.Barrier(2)
        def apply():
            start.wait(timeout=3)
            return self.p.apply(p['id'])
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            first, second = pool.submit(apply), pool.submit(apply)
            self.assertEqual(first.result(timeout=5), second.result(timeout=5))
        self.assertEqual(len(self.store.graph()['tickets']), 2)

    def test_basis_is_rechecked_after_journal_preparation_under_the_guard(self):
        p = self.mixed()
        original = self.store.preview
        def external_change(*args, **kwargs):
            preview = original(*args, **kwargs)
            self.path.write_bytes(self.raw.replace(b'Exact original', b'External change'))
            return preview
        with patch.object(self.store, 'preview', side_effect=external_change):
            with self.assertRaisesRegex(DomainError, 'expired'):
                self.p.apply(p['id'])
        self.assertEqual(self.p.get(p['id'])['status'], 'expired')
        self.assertEqual(len(self.store.graph()['tickets']), 1)
        self.assertFalse(self.book.path('n-premise').exists())
        self.assertIn(b'External change', self.path.read_bytes())
        self.assertFalse(self.attempts())


if __name__ == '__main__':
    unittest.main()
