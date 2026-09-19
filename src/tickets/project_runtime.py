"""Explicit project launcher; importing this module never opens application data."""
from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path
import socket
import sys
import urllib.error
import urllib.request
import uuid

from .project_config import ProjectError, load, owned_path, resolve_path


def existing_instance(project: Path, config: dict, port: int, *, match_data=True) -> dict | None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for path in ['/api/instance', *config['status_aliases']]:
        try:
            with opener.open(f'http://127.0.0.1:{port}{path}', timeout=1) as response:
                current = json.load(response)
        except urllib.error.HTTPError:
            continue
        except (urllib.error.URLError, TimeoutError, OSError):
            continue
        except (ValueError, UnicodeError):
            raise ProjectError('The selected port did not return a Moldloft instance') from None
        if not isinstance(current, dict) or current.get('project') != str(project):
            raise ProjectError('The selected port belongs to another project')
        if not isinstance(current.get('instance'), str) or not current['instance'] or type(current.get('pid')) is not int:
            raise ProjectError('The selected port did not return a valid Moldloft instance identity')
        if not match_data:
            return current
        # Old project launchers identify only their workspace. Preserve that route
        # during adoption, but reject a changed data target in a new manifest.
        if 'db' in current and current['db'] != str(resolve_path(project, config['db'])):
            raise ProjectError('The running instance uses a different database')
        if 'notes' in current and current['notes'] != str(resolve_path(project, config['notes'])):
            raise ProjectError('The running instance uses a different notebook')
        if 'db' not in current and current.get('workspace') != str(resolve_path(project, config['notes']).parent):
            raise ProjectError('The running legacy instance uses a different workspace')
        return current
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', port))
        except OSError as exc:
            raise ProjectError('The selected port is occupied; select another port') from exc
    return None


def create_project_app(project: Path, config: dict, port: int):
    # These deliberate defaults apply only to the launched process. Do not read a
    # caller's private .env or silently adopt its database/model configuration.
    os.environ.pop('TICKETS_ENV_FILE', None)
    os.environ['TICKETS_TRANSLATION'] = 'off'
    os.environ['TICKETS_COPILOT'] = 'off'
    db = resolve_path(project, config['db'])
    notes = resolve_path(project, config['notes'])
    os.environ['TICKETS_DB'], os.environ['TICKETS_NOTES'] = str(db), str(notes)
    from .app import create_app
    app = create_app(db_path=db, notes_path=notes, repo_roots={}, translator=None)
    record = dict(project=str(project), workspace=str(notes.parent), db=str(db), notes=str(notes),
                  pid=os.getpid(), instance=uuid.uuid4().hex, environment=sys.prefix,
                  version=importlib.metadata.version('moldloft'),
                  url=f'http://127.0.0.1:{port}{config["start_path"]}',
                  translation='off', copilot='off')

    def status():
        return record

    for route in ['/api/instance', *config['status_aliases']]:
        app.add_api_route(route, status, methods=['GET'])
        # The MCP transport includes a catch-all mount.
        app.router.routes.insert(0, app.router.routes.pop())
    return app, record


def run_project(project: str | Path, port: int | None = None) -> None:
    project = Path(project).resolve()
    config = load(project)
    environment = resolve_path(project, config['environment'])
    if Path(sys.prefix).resolve() != environment:
        raise ProjectError('Use the configured project environment or tools/project.py run --project <root>')
    port = config['port'] if port is None else port
    if not 1 <= port <= 65535:
        raise ProjectError('Port must be between 1 and 65535')
    current = existing_instance(project, config, port)
    if current is not None:
        print(f'Moldloft is already running: {current.get("url", f"http://127.0.0.1:{port}/graph")}')
        return
    app, record = create_project_app(project, config, port)
    receipt = owned_path(project, config['process_record'])
    receipt.parent.mkdir(parents=True, exist_ok=True)
    temporary = receipt.with_name(f'.{receipt.name}.{record["instance"]}.tmp')
    try:
        temporary.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
        temporary.replace(receipt)
        print(record['url'], flush=True)
        from .server import run
        run(app, host='127.0.0.1', port=port, reload=False)
    finally:
        temporary.unlink(missing_ok=True)
        try:
            saved = json.loads(receipt.read_text(encoding='utf-8'))
            if saved.get('instance') == record['instance']:
                receipt.unlink()
        except (FileNotFoundError, ValueError):
            pass
