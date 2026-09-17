"""Markdown authority, conflicts, relation semantics and bounded Copilot context."""
import concurrent.futures
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient

from tickets.app import create_app
from tickets.db import initialize
from tickets.notes import Notebook, encode
from tickets.service import Store, DomainError
from tickets import copilot


class NotesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.engine = initialize(self.root / 'tickets.db')
        self.book = Notebook(self.root / 'notes', self.engine)
        self.store = Store(self.engine, notes=self.book)
        for title in ('API', 'UI', 'Independent'):
            self.store.mutate([{'op': 'create', 'fields': {'title': title}}])

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def note(self, name='n-premise', kind='assumption', state='adopted', relations=None):
        return self.book.write(name, dict(title='Synthetic premise', kind=kind, state=state,
            body='A deliberately authored statement.\n', relations=relations or []), None, {1, 2, 3})

    def test_markdown_is_authority_and_retries_are_idempotent(self):
        before = self.store.revision()
        note = self.note()
        path = self.root / 'notes' / 'n-premise.md'
        self.assertIn('A deliberately authored statement.', path.read_text(encoding='utf-8'))
        stamp = path.stat().st_mtime_ns
        self.assertEqual(self.book.write(note['id'], {'body': note['body']}, None, {1}), note)
        self.assertEqual(path.stat().st_mtime_ns, stamp)
        self.assertEqual(self.store.revision(), before)
        self.assertEqual(len(self.store.graph()['tickets']), 3)

    def test_legacy_notes_read_without_summaries_or_rewriting(self):
        path = self.root / 'notes' / 'n-legacy.md'
        path.parent.mkdir()
        raw = b'---\nid: "n-legacy"\ntitle: "Legacy note"\nkind: "question"\nstate: "open"\nrelations: []\n---\nKeep the English body.\n'
        path.write_bytes(raw)
        note = self.book.read('n-legacy')
        self.assertEqual((note['abstract_zh'], note['abstract_en']), ('', ''))
        self.assertEqual(path.read_bytes(), raw)

    def test_bilingual_abstracts_preserve_body_adoption_and_conflict_protection(self):
        note = self.note()
        fields = {'abstract_zh': '先验证 single-seat chat，再决定是否扩展；前提尚待判断。',
                  'abstract_en': 'Validate single-seat chat before expanding; the premise remains uncertain.'}
        updated = self.book.write(note['id'], fields, note['version'], {1})
        path = self.root / 'notes' / 'n-premise.md'
        self.assertIn(fields['abstract_zh'], path.read_text(encoding='utf-8'))
        self.assertEqual(updated['body'], note['body'])
        self.assertEqual(updated['adopted_digest'], note['adopted_digest'])
        self.assertEqual(updated['state'], 'adopted', 'an abstract edit does not reclassify the premise')
        self.assertEqual(self.book.write(note['id'], fields, note['version'], {1}), updated)
        with self.assertRaises(DomainError) as conflict:
            self.book.write(note['id'], {'abstract_zh': 'A stale draft'}, note['version'], {1})
        self.assertEqual(conflict.exception.status, 409)
        self.assertEqual(conflict.exception.details['current']['abstract_zh'], fields['abstract_zh'])
        changed = self.book.write(note['id'], {'body': 'A changed English premise.'}, updated['version'], {1})
        self.assertEqual(changed['state'], 'proposed')
        self.assertEqual(changed['abstract_zh'], fields['abstract_zh'], 'body edits never generate or overwrite abstracts')

    def test_invalid_abstracts_leave_authored_file_untouched(self):
        note = self.note()
        path = self.root / 'notes' / 'n-premise.md'
        before = path.read_bytes()
        for fields in ({'abstract_zh': None}, {'abstract_en': []}, {'abstract_zh': '字' * 601},
                       {'abstract_en': 'x' * 1201}, {'abstract_zh': 'Paragraph one\nParagraph two'}):
            with self.subTest(fields=fields):
                with self.assertRaises(DomainError):
                    self.book.write(note['id'], fields, note['version'], {1})
                self.assertEqual(path.read_bytes(), before)

    def test_external_edit_conflicts_and_invalid_file_is_visible(self):
        note = self.note()
        path = self.root / 'notes' / 'n-premise.md'
        path.write_bytes(encode({**note, 'body': 'Edited in another Markdown editor.'}))
        with self.assertRaises(DomainError) as conflict:
            self.book.write(note['id'], {'body': 'Stale browser text'}, note['version'], {1})
        self.assertEqual(conflict.exception.status, 409)
        self.assertEqual(conflict.exception.details['current']['body'], 'Edited in another Markdown editor.')
        path.write_text('Not valid frontmatter', encoding='utf-8')
        graph = self.store.graph()
        self.assertEqual(len(graph['tickets']), 3)
        self.assertEqual(graph['notes'], [])
        self.assertEqual(graph['notes_errors'][0]['file'], 'n-premise.md')
        with self.assertRaises(DomainError):
            self.book.write(note['id'], {'body': 'Overwrite'}, note['version'], {1})
        self.assertEqual(path.read_text(encoding='utf-8'), 'Not valid frontmatter')

    def test_assumptions_propagate_only_through_requires_and_assumes(self):
        self.store.mutate([{'op': 'edge_add', 'blocker_id': 1, 'blocked_id': 2}])
        premise = self.note(relations=[{'type': 'assumes', 'target': 'ticket:1'}, {'type': 'relates', 'target': 'ticket:3'}])
        graph = self.store.graph()
        self.assertEqual([t['assumptions'] for t in graph['tickets']], [['n-premise'], ['n-premise'], []])
        self.assertFalse(graph['tickets'][1]['ready'])
        self.book.write(premise['id'], {'state': 'refuted'}, premise['version'], {1, 2, 3})
        graph = self.store.graph()
        self.assertEqual(graph['tickets'][1]['review_notes'], ['n-premise'])
        self.assertEqual([t['status'] for t in graph['tickets']], ['open'] * 3)
        self.note('n-evidence', 'evidence', 'recorded', [{'type': 'supports', 'target': 'ticket:2'}])
        self.assertFalse(self.store.graph()['tickets'][1]['ready'])

    def test_soft_cycles_allowed_and_deleted_targets_remain_explicit(self):
        a = self.note('n-a', relations=[{'type': 'assumes', 'target': 'ticket:1'}])
        self.note('n-b', relations=[{'type': 'assumes', 'target': 'note:n-a'}])
        self.book.write('n-a', {'relations': [*a['relations'], {'type': 'assumes', 'target': 'note:n-b'}]}, a['version'], {1})
        self.assertEqual(len(self.store.graph()['tickets'][0]['assumptions']), 2)
        self.store.mutate([{'op': 'delete', 'ticket_id': 1}])
        graph = self.store.graph()
        self.assertTrue(next(r for r in graph['relations'] if r['target'] == 'ticket:1')['missing'])
        self.assertEqual(len(graph['notes']), 2)

    def test_adoption_is_bound_to_the_premise_text(self):
        note = self.note(relations=[{'type': 'assumes', 'target': 'ticket:1'}])
        self.assertFalse(note['adoption_stale'])
        changed = self.book.write(note['id'], {'body': 'A different premise'}, note['version'], {1})
        self.assertEqual(changed['state'], 'proposed')
        self.assertEqual(self.store.graph()['tickets'][0]['review_notes'], ['n-premise'])
        adopted = self.book.write(note['id'], {'state': 'adopted'}, changed['version'], {1})
        self.assertEqual(adopted['state'], 'adopted')
        self.assertEqual(self.store.graph()['tickets'][0]['review_notes'], [])

    def test_external_premise_change_requires_review_without_rewriting_file(self):
        note = self.note(relations=[{'type': 'assumes', 'target': 'ticket:1'}])
        path = self.root / 'notes' / 'n-premise.md'
        raw = encode({**note, 'body': 'Changed outside the application'})
        path.write_bytes(raw)
        current = self.book.read(note['id'])
        self.assertTrue(current['adoption_stale'])
        self.assertEqual(current['state'], 'proposed')
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(self.store.graph()['tickets'][0]['review_notes'], ['n-premise'])

    def test_invalid_relations_and_paths_never_write(self):
        for fields in (
            {'relations': [{'type': 'requires', 'target': 'ticket:1'}]},
            {'relations': [{'type': 'supports', 'target': 'ticket:1'}]},
            {'relations': [{'type': 'relates', 'target': 'ticket:999'}]},
            {'relations': [{'type': 'relates', 'target': '../../os/.env'}]},
        ):
            with self.assertRaises(DomainError):
                self.book.write('n-invalid', {'title': 'Example', **fields}, None, {1})
        for note_id in ('../escape', 'n-../../escape', 'n-CAPS'):
            with self.assertRaises(DomainError):
                self.book.read(note_id)
        self.assertEqual(self.book.snapshot()['notes'], [])

    def test_failed_replace_keeps_previous_bytes(self):
        note = self.note()
        path = self.root / 'notes' / 'n-premise.md'
        before = path.read_bytes()
        with patch('tickets.notes.os.replace', side_effect=OSError('fixture failure')):
            with self.assertRaises(DomainError) as error:
                self.book.write(note['id'], {'body': 'New body'}, note['version'], {1})
        self.assertEqual(error.exception.status, 503)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(path.parent.glob('.note-*.tmp')), [])

    def test_concurrent_note_saves_do_not_overwrite(self):
        note = self.note()
        def write(body):
            try:
                self.book.write(note['id'], {'body': body}, note['version'], {1})
                return True
            except DomainError as error:
                self.assertEqual(error.status, 409)
                return False
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(write, ('Editor A', 'Editor B'))), [False, True])

    def test_copilot_receives_only_attached_notes_and_checks_revision(self):
        note = self.note(relations=[{'type': 'assumes', 'target': 'ticket:1'}])
        note = self.book.write(note['id'], {'abstract_zh': '一个待验证的实验前提。',
            'abstract_en': 'An experimental premise to review.'}, note['version'], {1})
        self.note('n-unrelated')
        graph = self.store.graph()
        context, included = copilot.context_for(graph, selected_note='n-premise')
        self.assertEqual(included, {1})
        self.assertEqual([n['id'] for n in context['notes']], ['n-premise'])
        self.assertEqual(context['notes'][0]['abstract_zh'], note['abstract_zh'])
        self.assertEqual(context['notes'][0]['abstract_en'], note['abstract_en'])
        self.assertEqual(context['notes'][0]['body'], note['body'])
        self.book.write(note['id'], {'state': 'refuted'}, note['version'], {1})
        with self.assertRaises(DomainError):
            self.store.mutate([{'op': 'update', 'ticket_id': 1, 'fields': {'title': 'Stale proposal'}}],
                              notes_revision=graph['notes_revision'])
        self.assertEqual(self.store.graph()['tickets'][0]['title'], 'API')


class NotesApiTests(unittest.TestCase):
    def test_notes_are_isolated_and_stale_proposal_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'test.db'
            with TestClient(create_app(path, translator=None)) as client:
                client.post('/api/tickets', json={'fields': {'title': 'Fixture'}, 'revision': 0, 'request_id': str(uuid.uuid4())})
                response = client.put('/api/notes/n-question', json={'fields': {'title': 'Question', 'relations': [{'type': 'relates', 'target': 'ticket:1'}]}})
                self.assertEqual(response.status_code, 200)
                self.assertTrue((Path(tmp) / 'notes' / 'n-question.md').exists())
                graph = client.get('/api/graph').json()
                client.put('/api/notes/n-question', json={'fields': {'body': 'Changed'}, 'version': response.json()['version']})
                result = client.post('/api/copilot/apply', json={'operations': [{'op': 'update', 'ticket_id': 1, 'fields': {'title': 'Wrong'}}],
                    'revision': graph['revision'], 'notes_revision': graph['notes_revision'], 'context_ids': [1], 'request_id': str(uuid.uuid4())})
                self.assertEqual(result.status_code, 409)
                self.assertEqual(client.get('/api/graph').json()['tickets'][0]['title'], 'Fixture')
                current = client.get('/api/graph').json()
                batch = {'operations': [{'op': 'update', 'ticket_id': 1, 'fields': {'title': 'Acknowledged'}}],
                    'revision': current['revision'], 'notes_revision': current['notes_revision'], 'context_ids': [1], 'request_id': str(uuid.uuid4())}
                applied = client.post('/api/copilot/apply', json=batch)
                self.assertEqual(applied.status_code, 200)
                latest_note = client.get('/api/graph').json()['notes'][0]
                client.put('/api/notes/n-question', json={'fields': {'body': 'Later note edit'}, 'version': latest_note['version']})
                retry = client.post('/api/copilot/apply', json=batch)
                self.assertEqual(retry.status_code, 200)
                self.assertEqual(retry.json(), applied.json(), 'acknowledged retry precedes stale notebook checks')


if __name__ == '__main__':
    unittest.main()
