"""Standard-library project manifest shared by the installer and runtime."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import uuid

CONFIG = Path('.moldloft/project.json')
RECEIPT = Path('.moldloft/install.local.json')
HOSTS = ('codex', 'claude', 'zcode')


class ProjectError(ValueError):
    pass


def owned_path(project: Path, value: str | Path) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ProjectError('Installation paths must be nonempty strings')
    path = (project / value).resolve()
    if not path.is_relative_to(project.resolve()) or path == project.resolve():
        raise ProjectError(f'Installation path must stay inside the project: {value}')
    return path


def resolve_path(project: Path, value: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ProjectError('Project paths must be nonempty strings')
    return (project / Path(value).expanduser()).resolve()


def portable_path(project: Path, path: Path) -> str:
    path = path.resolve()
    return path.relative_to(project).as_posix() if path.is_relative_to(project) else str(path)


def python_path(environment: Path) -> Path:
    return environment / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')


def defaults(project: Path, source: Path) -> dict:
    return dict(schema_version=1, installation_id=uuid.uuid4().hex,
                source=portable_path(project, source), environment='.tools/moldloft',
                db='workspace/moldloft/data/tickets.db', notes='workspace/moldloft/notes',
                port=8787, hosts=[], principles=False, mode='editable',
                process_record='.moldloft/process.local.json', status_aliases=[],
                start_path='/graph?drydock=default')


def validate(project: Path, config: dict) -> dict:
    if not isinstance(config, dict) or config.get('schema_version') != 1:
        raise ProjectError('Unsupported project manifest; expected schema_version 1')
    required = set(defaults(project, project))
    if set(config) != required:
        raise ProjectError('Project manifest has missing or unknown fields')
    if not isinstance(config['installation_id'], str) or not config['installation_id'].isalnum():
        raise ProjectError('Invalid installation_id')
    for key in ('source', 'environment', 'process_record'):
        owned_path(project, config[key])
    source = resolve_path(project, config['source'])
    environment = resolve_path(project, config['environment'])
    if source.is_relative_to(environment) or environment.is_relative_to(source):
        raise ProjectError('Source and environment must be separate directories')
    for key in ('db', 'notes'):
        path = resolve_path(project, config[key])
        if path.is_relative_to(source) or path.is_relative_to(environment):
            raise ProjectError(f'{key} must be outside source and environment')
    db, notes = resolve_path(project, config['db']), resolve_path(project, config['notes'])
    record = resolve_path(project, config['process_record'])
    reserved = [source, environment, notes, *(project / p for p in ('.codex', '.claude', '.zcode'))]
    if (record.suffix != '.json' or record == db or any(record.is_relative_to(p) for p in reserved)
            or record in (project / CONFIG, project / RECEIPT, project / '.moldloft/hooks.local.json')):
        raise ProjectError('Process record must be a separate local JSON file outside data, source and host settings')
    if environment.is_relative_to(project / '.moldloft') or (project / '.moldloft').is_relative_to(environment):
        raise ProjectError('The virtual environment must be separate from project configuration')
    if db == notes or source.is_relative_to(notes) or environment.is_relative_to(notes):
        raise ProjectError('Notebook must be separate from source, environment and database')
    if type(config['port']) is not int or not 1 <= config['port'] <= 65535:
        raise ProjectError('Port must be between 1 and 65535')
    if config['mode'] not in ('editable', 'wheel') or type(config['principles']) is not bool:
        raise ProjectError('Invalid installation mode or principles option')
    hosts = config['hosts']
    if not isinstance(hosts, list) or any(h not in HOSTS for h in hosts) or len(set(hosts)) != len(hosts):
        raise ProjectError('Invalid agent hosts')
    aliases = config['status_aliases']
    if not isinstance(aliases, list) or any(not isinstance(p, str) or not p.startswith('/')
            or p.startswith('//') or any(c in p for c in '?#{}') or p in ('/', '/graph', '/api/instance')
            or p.startswith(('/api/', '/static/', '/mcp')) for p in aliases):
        raise ProjectError('Invalid instance status aliases')
    start = config['start_path']
    if not isinstance(start, str) or not start.startswith('/') or start.startswith('//'):
        raise ProjectError('start_path must be a local URL path')
    return config


def read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
    except (ValueError, UnicodeError) as exc:
        raise ProjectError(f'Invalid JSON: {path}') from exc
    if not isinstance(value, dict):
        raise ProjectError(f'Expected a JSON object: {path}')
    return value


def load(project: Path | str) -> dict:
    project = Path(project).resolve()
    config = read_json(owned_path(project, CONFIG))
    if config is None:
        raise ProjectError(f'No Moldloft project manifest in {project}; run the project installer')
    return validate(project, config)
