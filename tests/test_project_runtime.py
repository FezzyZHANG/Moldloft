"""Explicit project runtime, with temporary data and mocked port discovery."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from fastapi.testclient import TestClient
from tickets.project_config import CONFIG, ProjectError, defaults, load
from tickets.project_runtime import create_project_app, existing_instance, run_project


class ProjectRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.config = defaults(self.project, self.project / 'source')
        self.config['status_aliases'] = ['/fixture/status']
        target = self.project / CONFIG
        target.parent.mkdir()
        target.write_text(json.dumps(self.config), encoding='utf-8')
        env = patch.dict(os.environ, {key: value for key, value in os.environ.items()
                                    if not key.startswith(('TICKETS_', 'KIMI_', 'ZAI_', 'GLM_'))}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_manifest_load_has_no_data_side_effects(self):
        self.assertEqual(load(self.project), self.config)
        self.assertFalse((self.project / 'workspace').exists())

    def test_instance_assets_graph_and_restart_with_models_disabled(self):
        os.environ['TICKETS_ENV_FILE'] = str(self.project / 'must-not-read.env')
        os.environ['TICKETS_COPILOT'] = 'on'
        os.environ['TICKETS_TRANSLATION'] = 'on'
        app, identity = create_project_app(self.project, self.config, 8787)
        self.assertFalse((self.project / 'workspace').exists())
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/instance?drydock=not-a-graph').json(), identity)
            self.assertEqual(client.get('/fixture/status').json(), identity)
            for path in ('/', '/graph', '/static/graph.js', '/static/vendor/elk/elk-worker.min.js'):
                self.assertEqual(client.get(path).status_code, 200)
            graph = client.get('/api/graph').json()
            self.assertEqual(graph['tickets'], [])
            response = client.post('/api/tickets', json={'fields': {'title': 'Synthetic project'},
                'revision': graph['revision'], 'request_id': 'project-fixture-ticket'})
            self.assertEqual(response.status_code, 200)
        self.assertEqual(os.environ['TICKETS_COPILOT'], 'off')
        app, restarted = create_project_app(self.project, self.config, 8787)
        self.assertNotEqual(identity['instance'], restarted['instance'])
        with TestClient(app) as client:
            self.assertEqual(len(client.get('/api/graph').json()['tickets']), 1)

    def test_existing_identity_is_reused_without_opening_database(self):
        record = dict(project=str(self.project), workspace=str(self.project / 'workspace/moldloft'),
                      instance='existing', url='http://127.0.0.1:8787/graph')
        with patch('tickets.project_runtime.sys.prefix', str(self.project / '.tools/moldloft')), \
             patch('tickets.project_runtime.existing_instance', return_value=record), \
             patch('tickets.project_runtime.create_project_app') as create:
            run_project(self.project)
        create.assert_not_called()
        self.assertFalse((self.project / 'workspace').exists())

    def test_foreign_or_changed_workspace_is_not_reused(self):
        import io
        for record in ({'project': '/another'}, {'project': str(self.project), 'db': '/wrong'},
                       {'project': str(self.project), 'workspace': '/wrong'}):
            with patch('tickets.project_runtime.urllib.request.build_opener') as builder:
                builder.return_value.open.return_value = io.BytesIO(json.dumps(record).encode())
                with self.assertRaises(ProjectError):
                    existing_instance(self.project, self.config, 8787)

    def test_legacy_route_reused_and_unknown_occupied_port_rejected(self):
        import io
        record = {'project': str(self.project), 'workspace': str(self.project / 'workspace/moldloft'),
                  'instance': 'fixture-legacy', 'pid': 123}
        with patch('tickets.project_runtime.urllib.request.build_opener') as builder:
            builder.return_value.open.side_effect = [HTTPError('fixture', 404, 'missing', {}, None),
                                                    io.BytesIO(json.dumps(record).encode())]
            self.assertEqual(existing_instance(self.project, self.config, 8787), record)
        with patch('tickets.project_runtime.urllib.request.build_opener') as builder, \
             patch('tickets.project_runtime.socket.socket') as sock:
            builder.return_value.open.side_effect = URLError('fixture unavailable')
            sock.return_value.__enter__.return_value.bind.side_effect = OSError('occupied')
            with self.assertRaisesRegex(ProjectError, 'occupied'):
                existing_instance(self.project, self.config, 8787)

    def test_wrong_runtime_is_rejected_before_application_start(self):
        with self.assertRaisesRegex(ProjectError, 'configured project environment'):
            run_project(self.project)
        self.assertFalse((self.project / 'workspace').exists())


if __name__ == '__main__':
    unittest.main()
