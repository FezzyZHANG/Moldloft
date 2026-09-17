import copy
from pathlib import Path
import tempfile
import unittest

from tickets.db import initialize
from tickets.notes import Notebook
from tickets.service import Store, DomainError, reduce_graph
from tickets.note_ops import materialize
from tickets.copilot import validate_scope


class NoteOperationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.engine = initialize(self.root/'tickets.db')
        self.book = Notebook(self.root/'notes', self.engine)
        self.store = Store(self.engine, notes=self.book)
        self.store.mutate([{'op': 'create', 'fields': {'title': 'Existing'}}])

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def create(self, **fields):
        return {'op': 'note_create', 'temp_id': 'new:premise', 'path': 'notes/n-premise.md',
                'fields': {'title': 'Premise', 'kind': 'assumption', 'state': 'proposed', **fields}}

    def test_preview_is_pure_and_materializes_staged_targets(self):
        before = self.store.graph()
        operations = [{'op': 'create', 'temp_id': 'new:task', 'fields': {'title': 'Future'}},
                      self.create(relations=[{'type': 'assumes', 'target': 'ticket:new:task'}])]
        saved = copy.deepcopy(operations)
        result = self.store.preview(operations)
        note = result['preview']['notes'][0]
        self.assertEqual(note['relations'][0]['target'], 'ticket:new:task')
        self.assertEqual(materialize(note, {'new:task': 22})['relations'][0]['target'], 'ticket:22')
        self.assertEqual(self.store.graph(), before)
        self.assertEqual(operations, saved)
        self.assertIn('note', [c['kind'] for c in result['changes']])
        self.assertFalse(self.book.path('n-premise').exists())

    def test_create_cannot_reuse_identical_existing_note(self):
        self.book.write('n-premise', self.create()['fields'], None, {1})
        with self.assertRaisesRegex(DomainError, 'already exists'):
            self.store.preview([self.create()])
        with self.assertRaisesRegex(DomainError, 'already exists'):
            self.store.preview([self.create(), self.create()])

    def test_note_aliases_and_forward_relations_resolve(self):
        first = self.create(relations=[{'type': 'assumes', 'target': 'note:new:decision'}])
        second = {'op': 'note_create', 'temp_id': 'new:decision', 'path': 'notes/n-decision.md',
                  'fields': {'title': 'Choice', 'kind': 'decision', 'state': 'accepted'}}
        result = self.store.preview([first, second])['preview']
        premise = next(n for n in result['notes'] if n['id'] == 'n-premise')
        self.assertEqual(premise['relations'][0]['target'], 'note:n-decision')

    def test_invalid_kinds_paths_targets_and_deletion_are_rejected(self):
        cases = [self.create(kind='decision', state='accepted', relations=[{'type': 'assumes', 'target': 'ticket:1'}]),
                 self.create(relations=[{'type': 'assumes', 'target': 'ticket:new:missing'}]),
                 self.create(relations=[{'type': 'assumes', 'target': []}]),
                 {**self.create(), 'path': '../n-outside.md'},
                 {'op': 'note_delete', 'note_id': 'n-premise'},
                 {'op': 'note_update', 'note_id': 'n-absent', 'fields': {'body': 'Missing'}}]
        for op in cases:
            with self.subTest(op=op), self.assertRaises(DomainError):
                self.store.preview([op])

    def test_body_edit_requires_fresh_adoption_and_preserves_abstracts(self):
        self.book.write('n-premise', self.create(state='adopted', body='Old', abstract_en='Context')['fields'], None, {1})
        op = {'op': 'note_update', 'note_id': 'n-premise', 'fields': {'body': 'Changed'}}
        note = self.store.preview([op])['preview']['notes'][0]
        self.assertEqual(note['state'], 'proposed')
        self.assertEqual(note['abstract_en'], 'Context')
        op['fields']['state'] = 'adopted'
        note = self.store.preview([op])['preview']['notes'][0]
        self.assertEqual(note['state'], 'adopted')
        self.assertIn('adopted_digest', note)

    def test_public_ticket_apply_and_copilot_do_not_gain_note_write_path(self):
        with self.assertRaisesRegex(DomainError, 'reviewed proposal'):
            self.store.mutate([self.create()])
        with self.assertRaisesRegex(DomainError, 'ticket operations only'):
            validate_scope([self.create()], {1}, self.store.graph())
        self.assertFalse(self.book.snapshot()['notes'])

    def test_incomplete_notebook_prevents_note_proposal(self):
        graph = self.store.graph()
        graph['notes_errors'] = [{'file': 'n-bad.md', 'message': 'Invalid'}]
        with self.assertRaisesRegex(DomainError, 'incomplete'):
            reduce_graph(graph, [self.create()])


if __name__ == '__main__':
    unittest.main()
