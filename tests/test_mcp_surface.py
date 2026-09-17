import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient

from tickets.app import create_app
from tickets.mcp_surface import ReadTools


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.app = create_app(self.root/'tickets.db', translator=None, repo_roots={})
        self.client = TestClient(self.app, base_url='http://127.0.0.1:8792')
        self.client.__enter__()
        self.store = self.app.state.store
        self.store.mutate([{'op': 'create', 'fields': {'title': 'Bounded memory premise'}}])
        self.app.state.notes.write('n-mapped', {'title': 'Mapped memory', 'kind': 'assumption', 'state': 'adopted',
            'relations': [{'type': 'assumes', 'target': 'ticket:1'}]}, None, {1})
        self.app.state.notes.write('n-candidate', {'title': 'Memory evidence', 'kind': 'evidence', 'state': 'recorded',
            'abstract_en': 'Memory behavior observed in a fixture'}, None, {1})

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def rpc(self, method, params=None):
        response = self.client.post('/mcp', headers={'Accept': 'application/json, text/event-stream'},
            json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def call(self, name, arguments=None):
        response = self.rpc('tools/call', {'name': name, 'arguments': arguments or {}})
        result = response['result']
        self.assertFalse(result.get('isError'), result)
        return result.get('structuredContent') or json.loads(result['content'][0]['text'])

    def test_real_mcp_roundtrip_reads_and_retained_outcomes_without_approval_tools(self):
        initialized = self.rpc('initialize', {'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 'fixture', 'version': '1'}})
        self.assertIn('serverInfo', initialized['result'])
        tools = self.rpc('tools/list')['result']['tools']
        self.assertEqual({t['name'] for t in tools}, {'get_ready_work', 'get_ticket', 'get_note', 'search',
            'get_assumptions', 'list_review_hints', 'resolve_ticket', 'get_proposal', 'propose_changes'})
        self.assertTrue(all('Call' in t['description'] for t in tools))
        ticket = self.call('get_ticket', {'ticket_id': 1})
        note = self.call('get_note', {'note_id': 'n-mapped'})
        self.assertEqual(ticket['basis']['revision'], note['basis']['revision'])
        self.assertEqual(ticket['basis']['notes_revision'], note['basis']['notes_revision'])
        self.assertIn('n-mapped', ticket['basis']['notes'])
        captured = ticket['basis']
        proposal = self.app.state.proposals.submit(
            [{'op': 'update', 'ticket_id': 1, 'fields': {'title': 'Archived only'}}],
            captured, 'Retained record', 'Line one\nLine two', 'free', str(uuid.uuid4()))
        self.app.state.proposals.reject(proposal['id'], 'Keep the current title.')
        outcome = self.call('get_proposal', {'proposal_id': proposal['id']})
        self.assertEqual(outcome['rationale'], 'Line one\nLine two')
        self.assertEqual(outcome['status'], 'rejected')
        self.assertEqual(outcome['result']['reason'], 'Keep the current title.')
        for name in ('apply', 'approve', 'reject'):
            rejected = self.rpc('tools/call', {'name': name, 'arguments': {}})
            self.assertTrue(rejected['result']['isError'])
        self.assertEqual(self.store.graph()['tickets'][0]['title'], 'Bounded memory premise')

    def test_resolver_bands_candidates_and_never_builds_operations(self):
        before = self.store.graph()
        result = self.call('resolve_ticket', {'ticket_id': 1})
        self.assertTrue(result['has_premise_mapping'])
        direct = next(item for item in result['items'] if item['band'] == 'direct')
        candidate = next(item for item in result['items'] if item['band'] == 'candidate')
        self.assertTrue(direct['linked'])
        self.assertFalse(candidate['linked'])
        self.assertEqual(candidate['legal_relations'], ['supports', 'relates'])
        self.assertNotIn('operations', result)
        self.assertEqual(self.store.graph(), before)
        ready = self.call('get_ready_work')['items'][0]
        self.assertNotIn('assumptions', ready)
        self.assertNotIn('review_notes', ready)

    def test_truncation_is_visible_and_uses_conservative_basis(self):
        result = self.call('resolve_ticket', {'ticket_id': 1, 'limit': 1})
        self.assertTrue(result['truncated'])
        self.assertFalse(result['complete'])
        self.assertFalse(result['basis']['complete'])
        self.assertLess(len(json.dumps(result)), 65000)
        self.assertIn('n-candidate', json.dumps(self.call('search', {'query': 'memory', 'kind': 'evidence'})))

    def test_stale_basis_submission_is_rejected_without_writing(self):
        captured = self.call('get_ticket', {'ticket_id': 1})['basis']
        n = self.app.state.notes.read('n-mapped')
        self.app.state.notes.write('n-mapped', {'state': 'refuted'}, n['version'], {1})
        result = self.rpc('tools/call', {'name': 'propose_changes', 'arguments': {
            'operations': [{'op': 'update', 'ticket_id': 1, 'fields': {'title': 'Stale'}}],
            'basis': captured, 'base_revision': captured['revision'], 'notes_revision': captured['notes_revision'],
            'summary': 'Stale', 'rationale': 'Stale premise', 'origin': 'free', 'request_id': str(uuid.uuid4())}})
        self.assertTrue(result['result']['isError'])
        self.assertFalse(self.app.state.proposals.list()['items'])

    def test_missing_sdk_disables_only_mcp_and_host_opt_in_is_explicit(self):
        missing = ModuleNotFoundError("No module named mcp", name='mcp')
        with patch('tickets.mcp_surface.create_mcp', side_effect=missing):
            app = create_app(self.root/'no-mcp.db', translator=None, repo_roots={})
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/graph').status_code, 200)
            self.assertEqual(client.post('/mcp', json={}).status_code, 503)
        response = self.client.post('/mcp', headers={'Host': 'unapproved.invalid', 'Accept': 'application/json, text/event-stream'},
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
        self.assertEqual(response.status_code, 421)
        with patch.dict(os.environ, {'TICKETS_MCP_ALLOWED_HOSTS': 'moldloft.test:8792'}):
            app = create_app(self.root/'lan.db', translator=None, repo_roots={})
            with TestClient(app, base_url='http://moldloft.test:8792') as client:
                response = client.post('/mcp', headers={'Accept': 'application/json, text/event-stream'},
                    json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
                self.assertEqual(response.status_code, 200, response.text)


if __name__ == '__main__': unittest.main()
