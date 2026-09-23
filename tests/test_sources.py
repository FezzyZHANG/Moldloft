"""Provenance and counterevidence preserve human judgement and versioned writes."""
from pathlib import Path
import tempfile
import unittest
import uuid

from tickets import basis
from tickets.db import initialize
from tickets.mcp_surface import ReadTools
from tickets.notes import Notebook, encode
from tickets.proposals import Proposals
from tickets.service import Store, DomainError


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.engine = initialize(self.root / 'tickets.db')
        self.book = Notebook(self.root / 'notes', self.engine)
        self.store = Store(self.engine, notes=self.book)
        self.proposals = Proposals(self.store)
        self.store.mutate([{'op': 'create', 'fields': {'title': 'Review', 'status': 'done'}}])
        self.premise = self.write('n-premise', 'assumption', state='adopted',
            relations=[{'type': 'assumes', 'target': 'ticket:1'}])

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def write(self, ident, kind, **fields):
        return self.book.write(ident, {'title': ident, 'kind': kind, 'state': 'recorded',
            'body': 'Synthetic authored material.', **fields}, None, {1})

    def submit(self, operations):
        graph = self.store.graph()
        return self.proposals.submit(operations, basis.capture(graph, [1]), 'Review evidence',
            'Preserve provenance and assess counterevidence.', 'free', str(uuid.uuid4()))

    def evidence(self, source='n-source'):
        return self.write('n-evidence', 'evidence', relations=[
            {'type': 'derived_from', 'target': f'note:{source}'},
            {'type': 'challenges', 'target': 'note:n-premise'}])

    def test_source_and_counterevidence_do_not_refute_adopt_or_change_work(self):
        source = self.write('n-source', 'source', body='Fixture report, version 1. Table 2.')
        self.evidence()
        graph = self.store.graph()
        self.assertEqual(graph['notes_errors'], [])
        self.assertEqual(self.book.read('n-premise')['state'], 'adopted')
        self.assertEqual(self.book.read('n-premise')['adopted_digest'], self.premise['adopted_digest'])
        self.assertEqual(graph['tickets'][0]['status'], 'done')
        self.assertEqual(graph['tickets'][0]['review_notes'], [])
        self.assertEqual({r['type'] for r in graph['relations']}, {'assumes', 'challenges', 'derived_from'})
        self.assertEqual(ReadTools(self.store).search('n-source', kind='source')['items'][0]['id'], source['id'])
        updated = self.book.write(source['id'], {'state': 'superseded'}, source['version'], {1})
        self.assertEqual(updated['state'], 'superseded')
        self.assertEqual(self.book.read('n-evidence')['state'], 'recorded')

    def test_source_retry_conflict_and_captured_provenance(self):
        source = self.write('n-source', 'source')
        self.evidence()
        captured = ReadTools(self.store).get_note('n-evidence')['basis']
        self.assertIn('n-source', captured['notes'])
        op = {'op': 'note_update', 'note_id': 'n-evidence', 'fields': {'body': 'Checked table 2.'}}
        proposal = self.submit([op])
        updated = self.book.write(source['id'], {'body': 'Corrected fixture report.'}, source['version'], {1})
        stamp = self.book.path(source['id']).stat().st_mtime_ns
        self.assertEqual(self.book.write(source['id'], {'body': updated['body']}, source['version'], {1}), updated)
        self.assertEqual(self.book.path(source['id']).stat().st_mtime_ns, stamp)
        with self.assertRaises(DomainError):
            self.book.write(source['id'], {'body': 'Stale source draft'}, source['version'], {1})
        with self.assertRaises(DomainError):
            self.proposals.apply(proposal['id'])
        self.assertNotEqual(self.book.read('n-evidence')['body'], op['fields']['body'])

    def test_invalid_owners_targets_and_source_conversion_never_write(self):
        source = self.write('n-source', 'source')
        self.evidence()
        cases = [
            ('source', 'recorded', 'challenges', 'note:n-premise'),
            ('idea', 'open', 'derived_from', 'note:n-source'),
            ('evidence', 'recorded', 'derived_from', 'ticket:1'),
            ('evidence', 'recorded', 'derived_from', 'note:n-premise'),
            ('evidence', 'recorded', 'derived_from', 'note:n-absent'),
        ]
        for kind, state, relation, target in cases:
            with self.subTest(kind=kind, target=target), self.assertRaises(DomainError):
                self.write('n-invalid', kind, state=state, relations=[{'type': relation, 'target': target}])
        self.assertFalse(self.book.path('n-invalid').exists())
        raw = self.book.path(source['id']).read_bytes()
        with self.assertRaisesRegex(DomainError, 'source note'):
            self.book.write(source['id'], {'kind': 'metadata'}, source['version'], {1})
        self.assertEqual(self.book.path(source['id']).read_bytes(), raw)
        with self.assertRaisesRegex(DomainError, 'source note'):
            self.store.preview([{'op': 'note_update', 'note_id': source['id'], 'fields': {'kind': 'metadata'}}])

    def test_external_source_kind_conflict_is_visible_without_rewriting(self):
        source = self.write('n-source', 'source')
        self.evidence()
        raw = encode({**source, 'kind': 'metadata'})
        self.book.path(source['id']).write_bytes(raw)
        graph = self.store.graph()
        self.assertEqual(graph['notes_errors'][0]['file'], 'n-evidence.md')
        self.assertIn('source note', graph['notes_errors'][0]['message'])
        self.assertEqual(self.book.path(source['id']).read_bytes(), raw)
        with self.assertRaises(DomainError):
            self.store.preview([{'op': 'note_update', 'note_id': 'n-evidence', 'fields': {'body': 'Stale'}}])

    def test_external_kind_conflicts_can_be_repaired_one_source_at_a_time(self):
        for suffix in ('a', 'b'):
            source = self.write(f'n-source-{suffix}', 'source')
            self.write(f'n-evidence-{suffix}', 'evidence', relations=[
                {'type': 'derived_from', 'target': f'note:n-source-{suffix}'}])
            self.book.path(source['id']).write_bytes(encode({**source, 'kind': 'metadata'}))
        self.assertEqual(len(self.store.graph()['notes_errors']), 2)
        for suffix, remaining in [('a', 1), ('b', 0)]:
            current = self.book.read(f'n-source-{suffix}')
            self.book.write(current['id'], {'kind': 'source'}, current['version'], {1})
            self.assertEqual(len(self.store.graph()['notes_errors']), remaining)

    def test_missing_source_stays_inspectable_and_can_be_repaired(self):
        self.write('n-source', 'source')
        evidence = self.evidence()
        self.book.path('n-source').unlink()
        graph = self.store.graph()
        self.assertTrue(next(r for r in graph['relations'] if r['type'] == 'derived_from')['missing'])
        self.book.write(evidence['id'], {'body': 'The original report is unavailable.'}, evidence['version'], {1})
        with self.assertRaisesRegex(DomainError, 'source note'):
            self.write('n-source', 'question', state='open')
        self.write('n-source', 'source')
        self.assertFalse(next(r for r in self.store.graph()['relations'] if r['type'] == 'derived_from')['missing'])

    def test_forward_source_alias_mixed_apply_and_retry(self):
        operations = [
            {'op': 'create', 'temp_id': 'new:work', 'fields': {'title': 'Investigate counterevidence'}},
            {'op': 'note_create', 'temp_id': 'new:evidence', 'path': 'notes/n-a-evidence.md', 'fields': {
                'title': 'Counterevidence', 'kind': 'evidence', 'state': 'recorded', 'relations': [
                    {'type': 'derived_from', 'target': 'note:new:source'},
                    {'type': 'challenges', 'target': 'note:n-premise'},
                    {'type': 'relates', 'target': 'ticket:new:work'}]}},
            {'op': 'note_create', 'temp_id': 'new:source', 'path': 'notes/n-z-source.md', 'fields': {
                'title': 'Fixture report', 'kind': 'source', 'state': 'recorded', 'body': 'Table 2, version 1.'}},
        ]
        before = self.store.graph()
        proposal = self.submit(operations)
        self.assertEqual(self.store.graph(), before)
        result = self.proposals.apply(proposal['id'])
        self.assertEqual(self.proposals.apply(proposal['id']), result)
        evidence = self.book.read('n-a-evidence')
        self.assertEqual(evidence['relations'][0]['target'], 'note:n-z-source')
        self.assertEqual(self.book.read('n-premise')['state'], 'adopted')
        self.assertEqual(self.store.graph()['notes_errors'], [])
        self.assertEqual(len(self.store.graph()['tickets']), 2)

    def test_reviewed_conversion_validates_final_kinds_in_any_write_order(self):
        self.write('n-a-source', 'source')
        self.evidence('n-a-source')
        proposal = self.submit([
            {'op': 'note_update', 'note_id': 'n-a-source', 'fields': {'kind': 'metadata'}},
            {'op': 'note_update', 'note_id': 'n-evidence', 'fields': {'relations': [
                {'type': 'relates', 'target': 'note:n-a-source'}]}},
        ])
        self.proposals.apply(proposal['id'])
        self.assertEqual(self.book.read('n-a-source')['kind'], 'metadata')
        self.assertEqual(self.store.graph()['notes_errors'], [])
        self.assertEqual(self.store.graph()['metadata_relations'][0]['type'], 'relates')


if __name__ == '__main__':
    unittest.main()
