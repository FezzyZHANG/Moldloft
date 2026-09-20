"""Metadata indexes retain authored navigation without becoming dependency hubs."""
from pathlib import Path
import tempfile
import unittest
import uuid

from tickets import basis, copilot
from tickets.db import initialize
from tickets.mcp_surface import ReadTools
from tickets.notes import Notebook
from tickets.proposals import Proposals
from tickets.service import DomainError, Store


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.engine = initialize(self.root / 'tickets.db')
        self.book = Notebook(self.root / 'notes', self.engine)
        self.store = Store(self.engine, notes=self.book)
        self.proposals = Proposals(self.store)
        self.reads = ReadTools(self.store)
        self.store.mutate([{'op': 'create', 'fields': {'title': title}}
                           for title in ('Review experiment', 'Separate experiment', 'Independent')])

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def write(self, ident, **fields):
        return self.book.write(ident, {'title': 'Review index', 'kind': 'metadata',
            'state': 'recorded', 'body': 'Exact authored overview.\n', **fields}, None, {1, 2, 3})

    def hub(self):
        for i in range(25):
            self.write(f'n-item-{i}', title=f'Item {i}', kind='question', state='open')
        return self.write('n-index', relations=[{'type': 'relates', 'target': ref} for ref in
            ['ticket:1', 'ticket:2', 'ticket:3', *(f'note:n-item-{i}' for i in range(25))]])

    def test_conversion_preserves_authored_fields_and_versioned_retry_conflicts(self):
        original = self.write('n-index', kind='question', state='open', abstract_zh='阅读入口。',
            abstract_en='Reading entry.', relations=[{'type': 'relates', 'target': 'ticket:1'}])
        fields = {'kind': 'metadata', 'state': 'recorded'}
        converted = self.book.write('n-index', fields, original['version'], {1, 2, 3})
        for key in ('id', 'title', 'body', 'abstract_zh', 'abstract_en', 'relations'):
            self.assertEqual(converted[key], original[key])
        path = self.root / 'notes' / 'n-index.md'
        raw = path.read_bytes()
        self.assertEqual(self.book.write('n-index', fields, original['version'], {1, 2, 3}), converted)
        with self.assertRaises(DomainError) as caught:
            self.book.write('n-index', {'body': 'Stale draft'}, original['version'], {1, 2, 3})
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(path.read_bytes(), raw)
        for invalid in ({'state': 'adopted'}, {'relations': [{'type': 'assumes', 'target': 'ticket:1'}]}):
            with self.assertRaises(DomainError):
                self.book.write('n-index', invalid, converted['version'], {1, 2, 3})
            self.assertEqual(path.read_bytes(), raw)
        restored = self.book.write('n-index', {'kind': 'question', 'state': 'open'}, converted['version'], {1, 2, 3})
        self.assertEqual(restored['relations'], original['relations'])
        self.assertEqual(len(self.store.graph()['relations']), 1)

    def test_incident_references_are_navigation_with_missing_targets_preserved(self):
        index = self.write('n-index', relations=[{'type': 'relates', 'target': 'ticket:1'}])
        self.write('n-premise', kind='assumption', state='proposed', relations=[
            {'type': 'assumes', 'target': 'note:n-index'}, {'type': 'assumes', 'target': 'ticket:2'}])
        self.write('n-question', kind='question', state='open', relations=[{'type': 'relates', 'target': 'note:n-index'}])
        graph = self.store.graph()
        self.assertEqual([(r['source'], r['target']) for r in graph['relations']], [('note:n-premise', 'ticket:2')])
        self.assertEqual(len(graph['metadata_relations']), 3)
        self.assertEqual([t['assumptions'] for t in graph['tickets']], [[], ['n-premise'], []])
        self.assertEqual(basis.dependencies(graph, note_ids=['n-index']), (set(), {'n-index'}))
        self.assertEqual(basis.dependencies(graph, note_ids=['n-question']), (set(), {'n-question'}))
        self.store.mutate([{'op': 'delete', 'ticket_id': 1}])
        missing = next(r for r in self.store.graph()['metadata_relations'] if r['target'] == 'ticket:1')
        self.assertTrue(missing['missing'])
        self.assertEqual(self.book.read('n-index'), index, 'derived navigation never rewrites authored references')

    def test_large_index_does_not_expand_bounded_reads_or_candidate_resolution(self):
        index = self.hub()
        graph = self.store.graph()
        self.assertEqual(basis.dependencies(graph, [1]), ({1}, set()))
        ticket = self.reads.get_ticket(1)
        self.assertEqual([item['id'] for item in ticket['items']], [1])
        self.assertTrue(ticket['basis']['complete'])
        self.assertEqual(ticket['basis']['notes'], {})
        metadata = self.reads.get_note('n-index')
        self.assertEqual(metadata['items'][0]['relations'], index['relations'])
        self.assertEqual(metadata['basis']['tickets'], {})
        self.assertEqual(set(metadata['basis']['notes']), {'n-index'})
        self.assertTrue(metadata['basis']['complete'])
        self.assertEqual([item['id'] for item in self.reads.search('Review', kind='metadata')['items']], ['n-index'])
        self.assertEqual(self.reads.resolve_ticket(1)['candidate_count'], 0)
        self.write('n-unlinked-index')
        self.assertEqual(self.reads.resolve_ticket(1)['candidate_count'], 0, 'metadata is not a premise candidate')

    def test_copilot_requires_explicit_metadata_selection_without_following_its_index(self):
        self.hub()
        graph = self.store.graph()
        context, ids = copilot.context_for(graph, selected_id=1)
        self.assertEqual(ids, {1})
        self.assertEqual(context['notes'], [])
        context, ids = copilot.context_for(graph, selected_note='n-index')
        self.assertEqual(ids, set())
        self.assertEqual([n['id'] for n in context['notes']], ['n-index'])
        context, ids = copilot.context_for(graph, selected_note='n-index', references=[2])
        self.assertEqual(ids, {2}, 'an explicitly attached ticket remains available')
        self.assertEqual([n['id'] for n in context['notes']], ['n-index'])

    def test_preview_and_reviewed_conversion_keep_identity_and_retry_receipt(self):
        note = self.write('n-index', kind='question', state='open', relations=[{'type': 'relates', 'target': 'ticket:1'}])
        operations = [{'op': 'note_update', 'note_id': 'n-index', 'fields': {'kind': 'metadata', 'state': 'recorded'}}]
        captured = basis.capture(self.store.graph(), note_ids=['n-index'])
        proposal = self.proposals.submit(operations, captured, 'Classify index', 'Keep navigation outside the graph', 'free', str(uuid.uuid4()))
        preview = self.store.preview(operations)['preview']
        self.assertEqual(preview['relations'], [])
        self.assertEqual(len(preview['metadata_relations']), 1)
        self.assertEqual(self.book.read('n-index'), note, 'preview and submission do not edit the notebook')
        self.assertEqual(proposal['status'], 'pending')
        # Simulate the human review action only against this disposable fixture.
        applied = self.proposals.apply(proposal['id'])
        self.assertEqual(applied['status'], 'applied')
        self.assertEqual(self.proposals.apply(proposal['id']), applied)
        self.assertEqual(self.book.read('n-index')['body'], note['body'])
        self.assertEqual(self.book.read('n-index')['relations'], note['relations'])

    def test_metadata_body_is_independent_but_kind_changes_revalidate_scoped_proposals(self):
        index = self.hub()
        operations = [{'op': 'update', 'ticket_id': 1, 'fields': {'title': 'Reviewed experiment'}}]
        captured = basis.capture(self.store.graph(), [1])
        updated = self.book.write('n-index', {'body': 'Updated reading guide.'}, index['version'], {1, 2, 3})
        basis.check(captured, self.store.graph(), operations)
        self.book.write('n-index', {'kind': 'question', 'state': 'open'}, updated['version'], {1, 2, 3})
        with self.assertRaises(DomainError):
            basis.check(captured, self.store.graph(), operations)
