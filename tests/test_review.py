"""In-graph proposal review: tracked files, pure reads, reasons and reviewed apply."""
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from tickets.app import create_app
from tickets import basis
from tickets.models import NoteWriteAttempt, MutationReceipt
from tickets.proposals import coherent_graph
from tickets.service import DomainError


def fence_lines(text):
    lines, inside = [], False
    for row in text.splitlines():
        if row == '```diff':
            inside = True
        elif row == '```':
            inside = False
        elif inside:
            lines.append(row)
    return lines


class ReviewSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.app = create_app(self.root/'tickets.db', translator=None, repo_roots={})
        self.client = TestClient(self.app, base_url='http://127.0.0.1:8792')
        self.client.__enter__()
        self.store, self.p, self.book = self.app.state.store, self.app.state.proposals, self.app.state.notes
        self.store.mutate([{'op': 'create', 'fields': {'title': 'A', 'group_name': 'alpha', 'description': 'first\nsecond\nthird'}},
                           {'op': 'create', 'fields': {'title': 'B', 'group_name': 'beta'}}])
        self.book.write('n-premise', {'title': 'Premise', 'kind': 'assumption', 'state': 'proposed',
            'body': 'Line one\nLine two', 'relations': [{'type': 'relates', 'target': 'ticket:2'}]}, None, {1, 2})

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def submit(self, ops, tickets=(1,), notes=(), origin='free'):
        captured = basis.capture(coherent_graph(self.store), tickets, notes)
        return self.p.submit(ops, captured, 'Review fixture', 'Why: first line\nSecond line.', origin, str(uuid.uuid4()))

    def file_for(self, proposal):
        return self.p.files.path(proposal)

    def test_mixed_batch_previews_both_stores_and_applies_reviewed_fields(self):
        proposal = self.submit([
            {'op': 'create', 'temp_id': 'new:work', 'fields': {'title': 'New work', 'group_name': 'gamma'}},
            {'op': 'note_create', 'temp_id': 'new:premise', 'path': 'notes/n-new.md', 'fields': {
                'title': 'New premise', 'kind': 'assumption', 'state': 'proposed', 'body': 'Review this claim.',
                'relations': [{'type': 'assumes', 'target': 'ticket:new:work'}]}}])
        self.assertEqual(len(self.store.graph()['tickets']), 2)
        self.assertIsNone(self.book.read('n-new'))
        item = self.client.get('/api/proposals').json()['items'][0]
        self.assertEqual(item['store'], 'mixed')
        self.assertEqual(item['groups'], ['gamma'])
        detail = self.client.get('/api/proposals/' + proposal['id']).json()
        shown = [line['text'] for section in detail['sections'] for line in section['lines']]
        self.assertEqual(shown, fence_lines(self.file_for(proposal).read_text(encoding='utf-8')))
        self.assertIn('+ relation: assumes ticket:new:work', shown)
        body = {'edits': [{'index': 0, 'fields': {'title': 'Reviewed work'}},
                          {'index': 1, 'fields': {'body': 'A bounded claim.'}}], 'note': 'Reviewed together.'}
        response = self.client.post('/api/proposals/' + proposal['id'] + '/apply', json=body)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()['result']
        tid = result['created']['new:work']
        self.assertEqual(self.store.graph()['tickets'][-1]['title'], 'Reviewed work')
        self.assertEqual(self.book.read('n-new')['relations'], [{'type': 'assumes', 'target': f'ticket:{tid}'}])
        self.assertEqual(self.book.read('n-new')['body'], 'A bounded claim.')
        self.assertEqual(len(result['amendments']), 2)
        self.assertEqual(result['note'], 'Reviewed together.')
        self.assertEqual(self.client.post('/api/proposals/' + proposal['id'] + '/apply', json=body).json()['result'], result)

    def test_submission_writes_tracked_file_equal_to_inspector_lines(self):
        proposal = self.submit([{'op': 'update', 'ticket_id': 1, 'fields': {'title': 'Proposed', 'description': 'first\nchanged\nthird'}}])
        path = self.file_for(proposal)
        self.assertEqual(path.parent, self.root/'review')
        text = path.read_text(encoding='utf-8')
        self.assertIn('# Proposal ' + proposal['id'], text)
        self.assertIn('Why: first line\nSecond line.', text)
        self.assertIn('- title: "A"', text)
        self.assertIn('+ title: "Proposed"', text)
        self.assertIn('@@ description @@', text)
        detail = self.client.get('/api/proposals/' + proposal['id']).json()
        self.assertTrue(detail['check']['applicable'])
        shown = [line['text'] for section in detail['sections'] for line in section['lines']]
        self.assertEqual(shown, fence_lines(text))
        self.assertEqual(detail['preview']['base_revision'], self.store.graph()['revision'])
        self.assertIn('tickets', detail['preview']['preview'])

    def test_reads_do_not_write_and_only_apply_persists_expiry(self):
        proposal = self.submit([{'op': 'update', 'ticket_id': 1, 'fields': {'title': 'Proposed'}}])
        self.store.mutate([{'op': 'update', 'ticket_id': 1, 'fields': {'title': 'Edited meanwhile'}}])
        queue = self.client.get('/api/proposals').json()
        item = queue['items'][0]
        self.assertFalse(item['check']['applicable'])
        self.assertIn('ticket 1', item['check']['reason'])
        self.assertEqual(item['status'], 'pending')
        self.assertEqual(queue['pending'], 1)
        detail = self.client.get('/api/proposals/' + proposal['id']).json()
        self.assertFalse(detail['check']['applicable'])
        # The stale proposal still shows what it proposed; the check is a separate fact.
        self.assertIn('+ title: "Proposed"', [l['text'] for s in detail['sections'] for l in s['lines']])
        self.assertEqual(self.p.get(proposal['id'])['status'], 'pending')
        response = self.client.post('/api/proposals/' + proposal['id'] + '/apply', json={})
        self.assertEqual(response.status_code, 409)
        self.assertIn('expired', response.json()['message'])
        stored = self.p.get(proposal['id'])
        self.assertEqual(stored['status'], 'expired')
        self.assertIn('ticket 1', stored['stale_reason'])
        self.assertIn('- status: expired', self.file_for(stored).read_text(encoding='utf-8'))
        self.assertEqual(self.store.graph()['tickets'][0]['title'], 'Edited meanwhile')

    def test_reject_requires_a_reason_that_the_agent_can_read_back(self):
        proposal = self.submit([{'op': 'update', 'ticket_id': 1, 'fields': {'title': 'Proposed'}}])
        self.assertEqual(self.client.post('/api/proposals/' + proposal['id'] + '/reject', json={}).status_code, 422)
        self.assertEqual(self.client.post('/api/proposals/' + proposal['id'] + '/reject', json={'reason': '  '}).status_code, 422)
        self.assertEqual(self.p.get(proposal['id'])['status'], 'pending')
        response = self.client.post('/api/proposals/' + proposal['id'] + '/reject', json={'reason': 'Duplicates existing work.'})
        self.assertEqual(response.status_code, 200, response.text)
        outcome = self.p.get(proposal['id'])
        self.assertEqual(outcome['status'], 'rejected')
        self.assertEqual(outcome['result']['reason'], 'Duplicates existing work.')
        text = self.file_for(outcome).read_text(encoding='utf-8')
        self.assertIn('### Reviewer reason', text)
        self.assertIn('Duplicates existing work.', text)
        self.assertEqual(self.store.graph()['tickets'][0]['title'], 'A')
        self.assertEqual(self.client.post('/api/proposals/' + proposal['id'] + '/apply', json={}).status_code, 409)

    def test_ticket_only_apply_with_reviewer_edits_records_amendments_and_retries(self):
        proposal = self.submit([{'op': 'update', 'ticket_id': 1, 'fields': {'title': 'Proposed', 'tech_notes': 'agent text'}},
                                {'op': 'edge_add', 'blocker_id': 2, 'blocked_id': 1}], tickets=(1, 2))
        body = {'edits': [{'index': 0, 'fields': {'title': 'Proposed, reviewed'}}], 'note': 'Trimmed the title.'}
        response = self.client.post('/api/proposals/' + proposal['id'] + '/apply', json=body)
        self.assertEqual(response.status_code, 200, response.text)
        outcome = response.json()
        self.assertEqual(outcome['status'], 'applied')
        graph = self.store.graph()
        self.assertEqual(graph['tickets'][0]['title'], 'Proposed, reviewed')
        self.assertEqual(graph['tickets'][0]['tech_notes'], 'agent text')
        self.assertIn({'blocker_id': 2, 'blocked_id': 1}, graph['edges'])
        self.assertEqual(outcome['result']['amendments'], [{'index': 0, 'target': 'ticket #1',
            'fields': {'title': {'proposed': 'Proposed', 'applied': 'Proposed, reviewed'}}}])
        self.assertEqual(outcome['result']['note'], 'Trimmed the title.')
        self.assertEqual(outcome['applied_revision'], graph['revision'])
        text = self.file_for(outcome).read_text(encoding='utf-8')
        self.assertIn('### Reviewer amendments', text)
        self.assertIn('+ title: "Proposed, reviewed"', text)
        again = self.client.post('/api/proposals/' + proposal['id'] + '/apply', json=body)
        self.assertEqual(again.json()['applied_revision'], graph['revision'])
        self.assertEqual(self.store.graph()['revision'], graph['revision'])
        with self.app.state.store.engine.connect() as connection:
            self.assertTrue(connection.execute(select(MutationReceipt.request_id).where(
                MutationReceipt.request_id == 'proposal-' + proposal['id'])).first())

    def test_invalid_reviewer_edits_change_nothing(self):
        proposal = self.submit([{'op': 'note_update', 'note_id': 'n-premise', 'fields': {'state': 'retired',
            'relations': [{'type': 'relates', 'target': 'ticket:1'}]}}], tickets=(1,), notes=['n-premise'])
        for edits in ([{'index': 0, 'fields': {'relations': []}}], [{'index': 3, 'fields': {'state': 'open'}}],
                      [{'index': 0, 'fields': {'title': 'not proposed'}}], [{'index': 0, 'fields': {'state': 5}}]):
            response = self.client.post('/api/proposals/' + proposal['id'] + '/apply', json={'edits': edits})
            self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.p.get(proposal['id'])['status'], 'pending')
        self.assertEqual(self.book.read('n-premise')['state'], 'proposed')

    def test_note_only_apply_uses_managed_writes_and_clears_its_journal(self):
        proposal = self.submit([
            {'op': 'note_create', 'temp_id': 'new:evidence', 'path': 'notes/n-evidence.md', 'fields': {
                'title': 'Observed', 'kind': 'evidence', 'state': 'recorded', 'body': 'Seen in a fixture.',
                'relations': [{'type': 'supports', 'target': 'note:n-premise'}]}},
            {'op': 'note_update', 'note_id': 'n-premise', 'fields': {'body': 'Line one\nLine two revised'}}],
            tickets=(2,), notes=['n-premise'], origin='resolve')
        detail = self.client.get('/api/proposals/' + proposal['id']).json()
        targets = [s['target'] for s in detail['sections']]
        self.assertEqual(targets, ['note n-evidence', 'note n-premise'])
        texts = [l['text'] for s in detail['sections'] for l in s['lines']]
        self.assertIn('+ relation: supports note:n-premise', texts)
        self.assertIn('- Line two', texts)
        self.assertIn('+ Line two revised', texts)
        response = self.client.post('/api/proposals/' + proposal['id'] + '/apply', json={'note': 'Accepted as recorded.'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['result']['notes'], ['n-evidence', 'n-premise'])
        self.assertEqual(self.book.read('n-evidence')['relations'], [{'type': 'supports', 'target': 'note:n-premise'}])
        self.assertEqual(self.book.read('n-premise')['body'], 'Line one\nLine two revised')
        with self.store.engine.connect() as connection:
            self.assertFalse(connection.execute(select(NoteWriteAttempt.id)).first())
        self.assertEqual(self.store.graph()['tickets'][0]['title'], 'A')

    def test_queue_groups_by_ticket_group_and_fingerprint_follows_status(self):
        first = self.submit([{'op': 'update', 'ticket_id': 1, 'fields': {'title': 'Alpha work'}}])
        before = self.p.fingerprint()
        second = self.submit([{'op': 'note_update', 'note_id': 'n-premise', 'fields': {'state': 'retired'}}], tickets=(2,), notes=['n-premise'])
        self.assertNotEqual(before, self.p.fingerprint())
        queue = self.client.get('/api/proposals').json()
        self.assertEqual(queue['pending'], 2)
        by_id = {item['id']: item for item in queue['items']}
        self.assertEqual(by_id[first['id']]['groups'], ['alpha'])
        self.assertEqual(by_id[first['id']]['store'], 'tickets')
        self.assertEqual(by_id[second['id']]['groups'], ['beta'])
        self.assertEqual(by_id[second['id']]['store'], 'notes')
        self.assertTrue(all(item['check']['applicable'] for item in queue['items']))
        stamp = self.p.fingerprint()
        self.p.reject(first['id'], 'Not now.')
        self.assertNotEqual(stamp, self.p.fingerprint())
        self.assertEqual(self.client.get('/api/proposals').json()['pending'], 1)

    def test_cleanup_removes_the_file_and_keeps_the_receipt(self):
        proposal = self.submit([{'op': 'update', 'ticket_id': 1, 'fields': {'title': 'Proposed'}}])
        self.p.reject(proposal['id'], 'Not needed.')
        path = self.file_for(self.p.get(proposal['id']))
        self.assertTrue(path.exists())
        self.assertEqual(self.p.clear(['rejected'])['count'], 1)
        self.assertTrue(path.exists())
        self.p.clear(['rejected'], apply=True)
        self.assertFalse(path.exists())
        self.assertTrue(self.p.get(proposal['id'])['cleared'])
        self.assertEqual(self.client.get('/api/proposals/' + proposal['id']).json()['proposal']['cleared'], True)

    def test_mcp_exposes_one_write_tool_and_the_human_path_completes_it(self):
        def rpc(method, params=None):
            response = self.client.post('/mcp', headers={'Accept': 'application/json, text/event-stream'},
                json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}})
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()['result']
        def call(name, arguments):
            result = rpc('tools/call', {'name': name, 'arguments': arguments})
            self.assertFalse(result.get('isError'), result)
            return result.get('structuredContent') or json.loads(result['content'][0]['text'])
        tools = {t['name'] for t in rpc('tools/list')['tools']}
        self.assertIn('propose_changes', tools)
        self.assertEqual(len(tools), 9)
        self.assertFalse({'apply', 'approve', 'reject', 'apply_proposal'} & tools)
        captured = call('get_ticket', {'ticket_id': 1})['basis']
        pending = call('propose_changes', {'operations': [{'op': 'update', 'ticket_id': 1, 'fields': {'title': 'Via MCP'}}],
            'basis': captured, 'base_revision': captured['revision'], 'notes_revision': captured['notes_revision'],
            'summary': 'MCP fixture', 'rationale': 'Round trip.', 'origin': 'free', 'request_id': 'mcp-review-fixture-1'})
        self.assertEqual(pending['status'], 'pending')
        self.assertEqual(self.store.graph()['tickets'][0]['title'], 'A')
        self.assertTrue(self.file_for(self.p.get(pending['proposal_id'])).exists())
        mixed = rpc('tools/call', {'name': 'propose_changes', 'arguments': {'operations': [
            {'op': 'update', 'ticket_id': 1, 'fields': {'title': 'Mixed'}},
            {'op': 'note_update', 'note_id': 'n-premise', 'fields': {'state': 'retired'}}],
            'basis': captured, 'base_revision': captured['revision'], 'notes_revision': captured['notes_revision'],
            'summary': 'Mixed', 'rationale': 'Review both stores.', 'origin': 'free', 'request_id': 'mcp-review-fixture-2'}})
        self.assertFalse(mixed.get('isError'), mixed)
        mixed_record = mixed.get('structuredContent') or json.loads(mixed['content'][0]['text'])
        self.assertEqual(mixed_record['status'], 'pending')
        self.assertEqual(self.store.graph()['tickets'][0]['title'], 'A')
        self.assertEqual(self.book.read('n-premise')['state'], 'proposed')
        self.client.post('/api/proposals/' + pending['proposal_id'] + '/reject', json={'reason': 'Prefer the current title.'})
        outcome = call('get_proposal', {'proposal_id': pending['proposal_id']})
        self.assertEqual(outcome['status'], 'rejected')
        self.assertEqual(outcome['result']['reason'], 'Prefer the current title.')


if __name__ == '__main__':
    unittest.main()
