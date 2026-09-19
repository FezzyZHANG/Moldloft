"""Installed-package smoke check, invoked only with an explicit temporary project."""
import argparse
import json
from pathlib import Path
import sys

from fastapi.testclient import TestClient
import tickets
from tickets.project_config import load, resolve_path
from tickets.project_runtime import create_project_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--mode', choices=('editable', 'wheel'), required=True)
    args = parser.parse_args()
    project = args.project.resolve()
    config = load(project)
    module = Path(tickets.__file__).resolve()
    source = resolve_path(project, config['source'])
    if args.mode == 'editable':
        assert module.is_relative_to(source / 'src'), module
    else:
        assert module.is_relative_to(Path(sys.prefix).resolve()), module
        assert not module.is_relative_to(source), module
    for attempt in range(2):
        app, identity = create_project_app(project, config, config['port'])
        with TestClient(app, base_url='http://127.0.0.1:8792') as client:
            for path in ('/', '/graph', '/static/graph.js', '/static/graph.css',
                         '/static/layouts/compact-layered.js', '/static/vendor/elk/elk-worker.min.js'):
                assert client.get(path).status_code == 200, path
            assert client.get('/api/instance').json() == identity
            graph = client.get('/api/graph').json()
            if not graph['tickets']:
                assert client.post('/api/tickets', json={'fields': {'title': 'Distribution fixture'},
                    'revision': graph['revision'], 'request_id': 'distribution-ticket'}).status_code == 200
                assert client.put('/api/notes/n-distribution', json={'fields': {'title': 'Synthetic evidence',
                    'body': 'Disposable packaging evidence only.'}, 'version': None}).status_code == 200
            else:
                assert graph['tickets'][0]['title'] == 'Distribution fixture'
                assert graph['notes'][0]['id'] == 'n-distribution'
            for method, params in [('initialize', {'protocolVersion': '2025-03-26', 'capabilities': {},
                    'clientInfo': {'name': 'distribution-fixture', 'version': '1'}}), ('tools/list', {})]:
                response = client.post('/mcp', headers={'Accept': 'application/json, text/event-stream'},
                    json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
                assert response.status_code == 200, response.text
                assert 'result' in response.json(), response.text
            assert not app.state.translation_enabled
    print(json.dumps({'mode': args.mode, 'application_mcp_assets_restart': 'passed'}))


if __name__ == '__main__':
    main()
