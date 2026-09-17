import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

from fastapi.testclient import TestClient
from tickets.app import create_app
from tickets import basis
from tickets.service import DomainError
from tickets.proposals import coherent_graph


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'tickets.db'
        self.app = create_app(self.path, translator=None, repo_roots={})
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.store, self.p = self.app.state.store, self.app.state.proposals
        self.store.mutate([{'op': 'create', 'fields': {'title': 'Existing'}}])

    def tearDown(self):
        self.client.__exit__(None, None, None); self.tmp.cleanup()

    def submit(self, note=False):
        ops = [{'op': 'note_create', 'temp_id': 'new:note', 'path': 'notes/n-review.md',
                'fields': {'title': 'Readable note', 'body': 'Visible Markdown change\n<script>alert(1)</script>'}}] if note else [
                {'op': 'update', 'ticket_id': 1, 'fields': {'title': 'Accepted'}}]
        captured = basis.capture(coherent_graph(self.store), [1])
        request_id = str(uuid.uuid4())
        args = (ops, captured, 'Review <script>literal</script>', 'Stored reason\nSecond line', 'free', request_id)
        return self.p.submit(*args), args

    def test_review_reads_change_nothing_and_html_review_pages_stay_removed(self):
        p, _ = self.submit(note=True)
        before = self.store.graph()
        queue = self.client.get('/api/proposals').json()
        self.assertEqual([item['id'] for item in queue['items']], [p['id']])
        self.assertTrue(queue['items'][0]['check']['applicable'])
        detail = self.client.get('/api/proposals/' + p['id']).json()
        self.assertEqual(detail['sections'][0]['target'], 'note n-review')
        self.assertIn('<script>alert(1)</script>', json.dumps(detail))
        for url in ('/proposals', '/proposals/' + p['id']):
            self.assertEqual(self.client.get(url).status_code, 404)
        for action in ('apply', 'reject'):
            self.assertEqual(self.client.post('/proposals/' + p['id'] + '/' + action).status_code, 404)
        self.assertEqual(self.client.post('/api/proposals/' + p['id'] + '/preview').status_code, 404)
        self.assertEqual(self.store.graph(), before)
        self.assertEqual(self.p.get(p['id'])['status'], 'pending')
        self.assertFalse(self.app.state.notes.read('n-review'))
        self.assertNotIn('href="/proposals"', self.client.get('/').text)
        self.assertNotIn('href="/proposals"', self.client.get('/graph').text)

    def test_cli_cleanup_preview_apply_preserves_pending_and_replay(self):
        retained, args = self.submit()
        self.p.reject(retained['id'], 'Superseded by a later edit.')
        pending, _ = self.submit()
        review_file = self.p.files.path(self.p.get(retained['id']))
        self.assertTrue(review_file.exists())
        command = [sys.executable, '-B', '-m', 'tickets', 'proposals', 'clear', '--status', 'rejected', '--db', str(self.path)]
        def run(extra=()):
            r = subprocess.run(command + list(extra), cwd=self.path.parent, capture_output=True, text=True, timeout=15)
            self.assertEqual(r.returncode, 0, r.stderr)
            return json.loads(r.stdout)
        self.assertEqual(run()['count'], 1)
        self.assertFalse(self.p.get(retained['id']).get('cleared'))
        self.assertEqual(run(['--apply'])['count'], 1)
        self.assertFalse(review_file.exists())
        outcome = self.p.get(retained['id'])
        self.assertTrue(outcome['cleared'])
        self.assertEqual(outcome['rationale'], '')
        self.assertEqual(self.p.submit(*args)['id'], retained['id'])
        self.assertEqual(self.p.get(retained['id'])['status'], 'rejected')
        self.assertEqual(self.p.list()['items'][0]['id'], pending['id'])
        self.assertEqual(run(['--apply'])['count'], 0)
        with self.assertRaises(DomainError): self.p.clear(['pending'], apply=True)


if __name__ == '__main__': unittest.main()
