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

    def write_config(self):
        (self.project / CONFIG).write_text(json.dumps(self.config), encoding='utf-8')

    def test_bind_is_optional_and_must_be_an_ip_literal(self):
        self.assertNotIn('bind', load(self.project))
        for value in ('0.0.0.0', '192.168.10.5', '::'):
            self.config['bind'] = value
            self.write_config()
            self.assertEqual(load(self.project)['bind'], value)
        for value in ('localhost', '', 8787, '192.168.10.5:8787'):
            self.config['bind'] = value
            self.write_config()
            with self.assertRaisesRegex(ProjectError, 'bind'):
                load(self.project)

    def test_lan_bind_sets_mcp_hosts_and_loopback_clears_caller_hosts(self):
        os.environ['TICKETS_MCP_ALLOWED_HOSTS'] = 'caller.example:1'
        app, identity = create_project_app(self.project, self.config, 8787)
        self.assertNotIn('TICKETS_MCP_ALLOWED_HOSTS', os.environ)
        self.assertEqual((identity['bind'], identity['url']), ('127.0.0.1', 'http://127.0.0.1:8787/graph?drydock=default'))
        body = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}
        headers = {'Accept': 'application/json, text/event-stream', 'Host': '192.168.10.5:8787'}
        with TestClient(app) as client:
            self.assertGreaterEqual(client.post('/mcp', json=body, headers=headers).status_code, 400)
        self.config['bind'] = '192.168.10.5'
        app, identity = create_project_app(self.project, self.config, 8787)
        self.assertEqual(os.environ['TICKETS_MCP_ALLOWED_HOSTS'], '192.168.10.5:8787')
        self.assertEqual((identity['bind'], identity['url']), ('192.168.10.5', 'http://192.168.10.5:8787/graph?drydock=default'))
        with TestClient(app) as client:
            self.assertEqual(client.post('/mcp', json=body, headers=headers).status_code, 200)

    def test_unspecified_bind_reaches_loopback_and_names_this_machine(self):
        from tickets.project_runtime import connect_host, lan_hosts
        self.config['bind'] = '0.0.0.0'
        self.assertEqual(connect_host(self.config), '127.0.0.1')
        with patch('tickets.project_runtime.socket.gethostname', return_value='aglibot'), \
             patch('tickets.project_runtime.socket.getaddrinfo', return_value=[(0, 0, 0, '', ('192.168.10.5', 0)),
                                                                                 (0, 0, 0, '', ('fe80::1%3', 0, 0, 3))]):
            self.assertEqual(lan_hosts(self.config, 8787), ['aglibot:8787', '192.168.10.5:8787'])
        self.config['bind'] = '::'
        self.assertEqual(connect_host(self.config), '::1')

    def test_specific_bind_probes_its_address_then_loopback(self):
        self.config['bind'] = '192.168.10.5'
        self.config['status_aliases'] = []
        with patch('tickets.project_runtime.urllib.request.build_opener') as builder, \
             patch('tickets.project_runtime.socket.socket') as sock:
            builder.return_value.open.side_effect = URLError('fixture unavailable')
            self.assertIsNone(existing_instance(self.project, self.config, 8787))
        urls = [call.args[0] for call in builder.return_value.open.call_args_list]
        self.assertEqual(urls, ['http://192.168.10.5:8787/api/instance', 'http://127.0.0.1:8787/api/instance'])
        sock.return_value.__enter__.return_value.bind.assert_called_once_with(('192.168.10.5', 8787))

    def test_run_listens_on_manifest_bind(self):
        self.config['bind'] = '0.0.0.0'
        self.write_config()
        with patch('tickets.project_runtime.sys.prefix', str(self.project / '.tools/moldloft')), \
             patch('tickets.project_runtime.existing_instance', return_value=None), \
             patch('tickets.project_runtime.create_project_app', return_value=(object(), dict(
                 instance='fixture', url='http://127.0.0.1:8787/graph', bind='0.0.0.0'))), \
             patch('tickets.server.run') as run:
            run_project(self.project)
        self.assertEqual(run.call_args.kwargs['host'], '0.0.0.0')
        self.assertFalse((self.project / '.moldloft/process.local.json').exists())

    def test_wrong_runtime_is_rejected_before_application_start(self):
        with self.assertRaisesRegex(ProjectError, 'configured project environment'):
            run_project(self.project)
        self.assertFalse((self.project / 'workspace').exists())


if __name__ == '__main__':
    unittest.main()
