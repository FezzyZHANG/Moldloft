"""Fault injection at the SQL/filesystem boundary; every fixture is disposable."""
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

from sqlalchemy import select

from tickets.db import initialize, SCHEMA_VERSION
from tickets.models import NoteWriteAttempt
from tickets.notes import Notebook
from tickets.service import DomainError, Store
from tickets import writes


class WriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.engine = initialize(self.root / 'tickets.db')
        self.book = Notebook(self.root / 'notes', self.engine)
        self.store = Store(self.engine, notes=self.book)
        self.note = self.book.write('n-first', {'title': 'Original', 'body': 'Before\n'}, None, set())
        self.path = self.book.path('n-first')
        # Byte-exact compensation must preserve formatting, BOM and CRLF too.
        self.path.write_bytes(b'\xef\xbb\xbf' + self.path.read_bytes().replace(b'\n', b'\r\n'))
        self.raw = self.path.read_bytes()
        self.note = self.book.read('n-first')

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def change(self, connection):
        return self.book.write_in_transaction(connection, 'n-first', {'body': 'After'}, self.note['version'], set())

    def records(self):
        with self.engine.connect() as connection:
            return connection.execute(select(NoteWriteAttempt.files_json)).scalars().all()

    def test_single_thread_reuses_transaction_and_staged_ticket_ids(self):
        with writes.transaction(self.engine, [self.path]) as connection:
            connection.exec_driver_sql('PRAGMA busy_timeout=1')
            connection.exec_driver_sql("UPDATE graph_meta SET revision=revision+1")
            self.book.write_in_transaction(connection, 'n-first', {'relations': [
                {'type': 'relates', 'target': 'ticket:42'}]}, self.note['version'], {42})
        self.assertEqual(self.store.revision(), 1)
        self.assertEqual(self.book.read('n-first')['relations'][0]['target'], 'ticket:42')
        self.assertFalse(self.records())

    def test_later_write_failure_restores_exact_bytes_and_prior_absence(self):
        new_path = self.book.path('n-new')
        with self.assertRaises(DomainError):
            with writes.transaction(self.engine, [self.path, new_path]) as connection:
                connection.exec_driver_sql('UPDATE graph_meta SET revision=9')
                self.change(connection)
                self.book.write_in_transaction(connection, 'n-new', {'title': 'New'}, None, set())
                self.book.write_in_transaction(connection, 'n-new', {'title': ''}, None, set())
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertFalse(new_path.exists())
        self.assertEqual(self.store.revision(), 0)
        self.assertFalse(self.records())

    def test_failed_commit_restores_files_and_sql(self):
        with patch.object(writes, '_commit', side_effect=OSError('commit failed')):
            with self.assertRaises(OSError):
                with writes.transaction(self.engine, [self.path]) as connection:
                    connection.exec_driver_sql('UPDATE graph_meta SET revision=8')
                    self.change(connection)
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertEqual(self.store.revision(), 0)
        self.assertFalse(self.records())

    def test_commit_succeeded_but_ack_failed_preserves_committed_data(self):
        def uncertain(connection):
            connection.commit()
            raise OSError('lost acknowledgement')
        with patch.object(writes, '_commit', side_effect=uncertain):
            with self.assertRaises(OSError):
                with writes.transaction(self.engine, [self.path]) as connection:
                    connection.exec_driver_sql('UPDATE graph_meta SET revision=7')
                    self.change(connection)
        self.assertEqual(self.store.revision(), 7)
        self.assertEqual(self.book.read('n-first')['body'], 'After')
        self.assertFalse(self.records())

    def test_compensation_failure_retains_evidence_and_blocks_all_writers_after_restart(self):
        with patch.object(writes, 'replace_bytes', side_effect=OSError('restore failed')):
            with self.assertRaisesRegex(DomainError, 'recovery failed'):
                with writes.transaction(self.engine, [self.path]) as connection:
                    self.change(connection)
                    raise ValueError('later failure')
        record = json.loads(self.records()[0])[0]
        self.assertEqual(base64.b64decode(record['before']), self.raw)
        self.engine.dispose()
        self.engine = initialize(self.root / 'tickets.db')
        self.book = Notebook(self.root / 'notes', self.engine)
        self.store = Store(self.engine, notes=self.book)
        actions = [lambda: self.book.write('n-other', {'title': 'Blocked'}, None, set()),
                   lambda: self.store.mutate([{'op': 'create', 'fields': {'title': 'Blocked'}}]),
                   lambda: self.store.retry_translation(1)]
        for action in actions:
            with self.assertRaisesRegex(DomainError, 'ordinary writes are blocked'):
                action()
        self.assertEqual(self.book.read('n-first')['body'], 'After')

    def test_external_edit_during_failed_attempt_is_preserved(self):
        with self.assertRaisesRegex(DomainError, 'recovery failed'):
            with writes.transaction(self.engine, [self.path]) as connection:
                self.change(connection)
                self.path.write_bytes(b'External edit to preserve')
                raise ValueError('later failure')
        self.assertEqual(self.path.read_bytes(), b'External edit to preserve')
        self.assertTrue(self.records())

    def test_second_api_writer_cannot_interleave_after_sql_rollback(self):
        entered, release, started = threading.Event(), threading.Event(), threading.Event()
        original = writes.compensate
        def paused(files):
            entered.set()
            self.assertTrue(release.wait(3))
            original(files)
        def failing_writer():
            with self.assertRaises(ValueError):
                with writes.transaction(self.engine, [self.path]) as connection:
                    self.change(connection)
                    raise ValueError('later failure')
        def next_writer():
            started.set()
            return self.book.write('n-first', {'body': 'Acknowledged second save'}, self.note['version'], set())
        with concurrent.futures.ThreadPoolExecutor(2) as pool, patch.object(writes, 'compensate', side_effect=paused):
            first = pool.submit(failing_writer)
            self.assertTrue(entered.wait(3))
            second = pool.submit(next_writer)
            self.assertTrue(started.wait(3))
            try:
                with self.assertRaises(concurrent.futures.TimeoutError):
                    second.result(timeout=.15)
            finally:
                release.set()
            first.result(timeout=3)
            self.assertEqual(second.result(timeout=3)['body'], 'Acknowledged second save')
        self.assertEqual(self.book.read('n-first')['body'], 'Acknowledged second save')

    def test_interrupted_worker_leaves_evidence_and_blocks_new_ticket_ids(self):
        code = '''
import os, sys
from pathlib import Path
from tickets.db import initialize
from tickets.notes import Notebook
from tickets.writes import transaction
root = Path(sys.argv[1])
engine = initialize(root / 'tickets.db')
book = Notebook(root / 'notes', engine)
with transaction(engine, [book.path('n-crash')]) as connection:
    connection.exec_driver_sql('UPDATE graph_meta SET next_ticket_id=2')
    book.write_in_transaction(connection, 'n-crash', {'title': 'Interrupted'}, None, {1})
    os._exit(23)
'''
        result = subprocess.run([sys.executable, '-B', '-c', code, str(self.root)],
                                cwd=self.root, timeout=15, capture_output=True)
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertTrue(self.book.path('n-crash').exists())
        record = json.loads(self.records()[0])[0]
        self.assertIsNone(record['before'])
        with self.assertRaisesRegex(DomainError, 'ordinary writes are blocked'):
            self.store.mutate([{'op': 'create', 'fields': {'title': 'Cannot reuse staged ID'}}])

    def test_unmanaged_or_undeclared_connection_cannot_replace(self):
        with self.engine.connect() as connection:
            connection.exec_driver_sql('BEGIN IMMEDIATE')
            with self.assertRaisesRegex(DomainError, 'managed transaction'):
                self.change(connection)
        with writes.transaction(self.engine) as connection:
            with self.assertRaisesRegex(DomainError, 'managed transaction'):
                self.change(connection)
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_legacy_planned_recovery_survives_review_withdrawal(self):
        intended = b'Interrupted proposal content'
        self.path.write_bytes(intended)
        evidence = {'files': [{'path': str(self.path),
            'before': base64.b64encode(self.raw).decode(),
            'after': base64.b64encode(intended).decode()}],
            'context': {'proposal_id': 'withdrawn-fixture', 'assigned_ids': {}}}
        with self.engine.begin() as connection:
            connection.execute(NoteWriteAttempt.__table__.insert().values(
                id='legacy-planned-fixture', files_json=json.dumps(evidence)))
        with self.assertRaisesRegex(DomainError, 'ordinary writes are blocked'):
            self.store.mutate([{'op': 'create', 'fields': {'title': 'Blocked'}}])
        writes.recover_pending(self.engine, self.book.root)
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertFalse(self.records())
        writes.recover_pending(self.engine, self.book.root)
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_schema_v1_upgrade_preserves_rows_and_takes_backup(self):
        self.store.mutate([{'op': 'create', 'fields': {'title': 'Survive upgrade'}}])
        with self.engine.begin() as connection:
            connection.exec_driver_sql('DROP TABLE note_write_attempts')
            connection.exec_driver_sql('PRAGMA user_version=1')
        self.engine.dispose()
        self.engine = initialize(self.root / 'tickets.db')
        self.store = Store(self.engine)
        self.assertEqual(self.store.graph()['tickets'][0]['title'], 'Survive upgrade')
        self.assertEqual(len(list((self.root / 'backups').glob(f'tickets-before-v{SCHEMA_VERSION}-*.sqlite3'))), 1)
        self.assertFalse(self.records())

    def test_guard_is_shared_with_an_independent_process(self):
        code = '''
import sys
from pathlib import Path
from tickets.db import initialize
from tickets.service import Store
root = Path(sys.argv[1])
engine = initialize(root / 'tickets.db')
print('ready', flush=True)
Store(engine).mutate([{'op': 'create', 'fields': {'title': 'Other process'}}])
print('saved', flush=True)
engine.dispose()
'''
        with writes.writer_gate(self.engine):
            process = subprocess.Popen([sys.executable, '-B', '-c', code, str(self.root)],
                cwd=self.root, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(process.stdout.readline().strip(), 'ready')
                with self.assertRaises(subprocess.TimeoutExpired):
                    process.wait(timeout=.15)
            except BaseException:
                process.kill()
                process.communicate()
                raise
        stdout, stderr = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn('saved', stdout)
        self.assertEqual(self.store.graph()['tickets'][0]['title'], 'Other process')


if __name__ == '__main__':
    unittest.main()
