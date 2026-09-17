import copy
from pathlib import Path
import tempfile
import unittest
import uuid

from tickets.db import initialize
from tickets.notes import Notebook
from tickets.service import Store, DomainError
from tickets.proposals import Proposals, coherent_graph
from tickets import basis


class ProposalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'tickets.db'
        self.open()
        self.store.mutate([{'op': 'create', 'fields': {'title': 'A'}}, {'op': 'create', 'fields': {'title': 'B'}}])

    def open(self):
        self.engine = initialize(self.path)
        self.book = Notebook(self.path.parent/'notes', self.engine)
        self.store = Store(self.engine, notes=self.book)
        self.proposals = Proposals(self.store)

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def submit(self, tid=1, captured=None, request_id=None, **kwargs):
        graph = coherent_graph(self.store)
        return self.proposals.submit([{'op': 'update', 'ticket_id': tid, 'fields': {'title': 'Proposed'}}],
            captured or basis.capture(graph, [tid]), kwargs.get('summary', 'Review this change'),
            kwargs.get('rationale', 'First line\nSecond line: retain exact reasoning.'), 'free', request_id or str(uuid.uuid4()))

    def test_restart_retains_exact_basis_rationale_and_idempotent_replay(self):
        captured = basis.capture(coherent_graph(self.store), [1])
        saved = copy.deepcopy(captured)
        request_id = str(uuid.uuid4())
        proposal = self.submit(captured=captured, request_id=request_id)
        self.engine.dispose(); self.open()
        after = self.proposals.get(proposal['id'])
        self.assertEqual(after['basis'], saved)
        self.assertEqual(after['rationale'], proposal['rationale'])
        self.assertEqual(self.submit(captured=captured, request_id=request_id)['id'], proposal['id'])
        captured['complete'] = False
        with self.assertRaisesRegex(DomainError, 'different content or preconditions'):
            self.submit(captured=captured, request_id=request_id)
        self.assertEqual(self.store.graph()['tickets'][0]['title'], 'A')

    def test_disjoint_field_change_survives_without_rebasing_captured_basis(self):
        one, two = self.submit(1), self.submit(2)
        self.store.mutate([{'op': 'update', 'ticket_id': 1, 'fields': {'title': 'Accepted sibling'}}])
        result = self.proposals.validate(two['id'])
        self.assertEqual(result['proposal']['status'], 'pending')
        self.assertEqual(result['check']['mode'], 'scoped')
        self.assertEqual(result['proposal']['basis'], two['basis'])
        stale = self.proposals.validate(one['id'])
        self.assertFalse(stale['check']['applicable'])
        # Reading never writes; the stored status changes only through an explicit action.
        self.assertEqual(self.proposals.get(one['id'])['status'], 'pending')

    def test_read_only_premise_change_expires_target_with_same_ticket_version(self):
        premise = self.book.write('n-premise', {'title': 'Dependency', 'kind': 'assumption', 'state': 'adopted',
            'relations': [{'type': 'assumes', 'target': 'ticket:2'}]}, None, {1, 2})
        proposal = self.submit(2)
        self.assertIn('n-premise', proposal['basis']['notes'])
        version = self.store.graph()['tickets'][1]['version']
        self.book.write('n-premise', {'state': 'refuted'}, premise['version'], {1, 2})
        check = self.proposals.validate(proposal['id'])['check']
        self.assertFalse(check['applicable'])
        self.assertIn('note n-premise', check['reason'])
        with self.assertRaisesRegex(DomainError, 'note n-premise'):
            self.proposals.apply(proposal['id'])
        result = self.proposals.get(proposal['id'])
        self.assertEqual(result['status'], 'expired')
        self.assertIn('note n-premise', result['stale_reason'])
        self.assertEqual(self.store.graph()['tickets'][1]['version'], version)

    def test_relation_membership_and_prerequisite_changes_expire(self):
        proposal = self.submit(2)
        self.store.mutate([{'op': 'edge_add', 'blocker_id': 1, 'blocked_id': 2}])
        self.assertFalse(self.proposals.validate(proposal['id'])['check']['applicable'])
        proposal = self.submit(2)
        self.book.write('n-new', {'title': 'New assumption', 'kind': 'assumption', 'state': 'proposed',
            'relations': [{'type': 'assumes', 'target': 'ticket:2'}]}, None, {1, 2})
        self.assertFalse(self.proposals.validate(proposal['id'])['check']['applicable'])

    def test_incomplete_scope_keeps_global_guard(self):
        captured = basis.capture(coherent_graph(self.store), [2], complete=False)
        proposal = self.submit(2, captured=captured)
        self.store.mutate([{'op': 'update', 'ticket_id': 1, 'fields': {'title': 'Unrelated'}}])
        self.assertFalse(self.proposals.validate(proposal['id'])['check']['applicable'])

    def test_declared_read_ticket_is_distinct_from_write_target(self):
        captured = basis.capture(coherent_graph(self.store), [1, 2])
        proposal = self.submit(2, captured=captured)
        self.store.mutate([{'op': 'update', 'ticket_id': 1, 'fields': {'tech_notes': 'Evidence changed'}}])
        check = self.proposals.validate(proposal['id'])['check']
        self.assertFalse(check['applicable'])
        self.assertIn('ticket 1', check['reason'])

    def test_stale_submission_rejected_and_input_bounds_not_silently_trimmed(self):
        captured = basis.capture(coherent_graph(self.store), [1])
        self.store.mutate([{'op': 'update', 'ticket_id': 1, 'fields': {'title': 'Changed'}}])
        with self.assertRaisesRegex(DomainError, 'expired'):
            self.submit(captured=captured)
        for kwargs in [{'summary': 'Two\nLines'}, {'rationale': 'x'*32001}]:
            with self.assertRaises(DomainError): self.submit(**kwargs)
        self.assertFalse(self.proposals.list()['items'])

    def test_queue_defaults_to_pending_and_limits_without_losing_outcomes(self):
        first, second = self.submit(1), self.submit(2)
        self.assertTrue(self.proposals.list(limit=1)['truncated'])
        self.store.mutate([{'op': 'update', 'ticket_id': 1, 'fields': {'title': 'Changed'}}])
        checked = {p['id']: p['check']['applicable'] for p in self.proposals.list(check=True)['items']}
        self.assertEqual(checked, {first['id']: False, second['id']: True})
        with self.assertRaisesRegex(DomainError, 'expired'):
            self.proposals.apply(first['id'])
        self.assertEqual([p['id'] for p in self.proposals.list()['items']], [second['id']])
        self.assertEqual(self.proposals.list(status='expired')['items'][0]['id'], first['id'])


if __name__ == '__main__': unittest.main()
