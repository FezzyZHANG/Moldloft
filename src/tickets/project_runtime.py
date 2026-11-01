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

from .project_config import ProjectError, bind_address, load, owned_path, resolve_path


def connect_host(config: dict) -> str:
    """The address this machine uses to reach the configured listener."""
    address = bind_address(config)
    if address.is_unspecified:
        return '::1' if address.version == 6 else '127.0.0.1'
    return str(address)


def url_host(host: str) -> str:
    return f'[{host}]' if ':' in host else host


def lan_hosts(config: dict, port: int) -> list[str]:
    """MCP Host values for a non-loopback bind; an unspecified bind names this machine's addresses."""
    address = bind_address(config)
    if address.is_loopback:
        return []
    if not address.is_unspecified:
        return [f'{url_host(str(address))}:{port}']
    name = socket.gethostname()
    try:
        found = {info[4][0] for info in socket.getaddrinfo(name, None)}
    except OSError:
        found = set()
    return [f'{url_host(h)}:{port}' for h in [name, *sorted(found)] if '%' not in h]


def existing_instance(project: Path, config: dict, port: int, *, match_data=True) -> dict | None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    # A launch with an earlier bind may still serve the same data on loopback.
    hosts = dict.fromkeys([connect_host(config), '127.0.0.1'])
    for host, path in ((h, p) for h in hosts for p in ['/api/instance', *config['status_aliases']]):
        try:
            with opener.open(f'http://{url_host(host)}:{port}{path}', timeout=1) as response:
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
    address = bind_address(config)
    with socket.socket(socket.AF_INET6 if address.version == 6 else socket.AF_INET) as probe:
        try:
            probe.bind((str(address), port))
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
    # MCP Host validation follows the manifest bind, not a caller's environment.
    allowed = lan_hosts(config, port)
    if allowed:
        os.environ['TICKETS_MCP_ALLOWED_HOSTS'] = ','.join(allowed)
    else:
        os.environ.pop('TICKETS_MCP_ALLOWED_HOSTS', None)
    from .app import create_app
    app = create_app(db_path=db, notes_path=notes, repo_roots={}, translator=None)
    record = dict(project=str(project), workspace=str(notes.parent), db=str(db), notes=str(notes),
                  pid=os.getpid(), instance=uuid.uuid4().hex, environment=sys.prefix,
                  version=importlib.metadata.version('moldloft'),
                  url=f'http://{url_host(connect_host(config))}:{port}{config["start_path"]}',
                  bind=str(bind_address(config)), translation='off', copilot='off')

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
        fallback = f'http://{url_host(connect_host(config))}:{port}/graph'
        print(f'Moldloft is already running: {current.get("url", fallback)}')
        return
    app, record = create_project_app(project, config, port)
    receipt = owned_path(project, config['process_record'])
    receipt.parent.mkdir(parents=True, exist_ok=True)
    temporary = receipt.with_name(f'.{receipt.name}.{record["instance"]}.tmp')
    try:
        temporary.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
        temporary.replace(receipt)
        print(record['url'], flush=True)
        if not bind_address(config).is_loopback:
            print(f'Listening on {url_host(record["bind"])}:{port} without authentication', file=sys.stderr, flush=True)
        from .server import run
        run(app, host=record['bind'], port=port, reload=False)
    finally:
        temporary.unlink(missing_ok=True)
        try:
            saved = json.loads(receipt.read_text(encoding='utf-8'))
            if saved.get('instance') == record['instance']:
                receipt.unlink()
        except (FileNotFoundError, ValueError):
            pass
