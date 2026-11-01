"""Install, check or run Moldloft in an explicit containing project (Python 3.10+).

The installer uses only the standard library. Package installation is the only
networked stage; checks and hooks never install dependencies or open app data.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import quote, urlparse
from urllib.request import url2pathname
import venv

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / 'src'))
sys.path.insert(0, str(SOURCE / 'tools/agent_hooks'))
from tickets.project_config import (CONFIG, RECEIPT, HOSTS, ProjectError, defaults, load,
                                    owned_path, portable_path, python_path, read_json,
                                    resolve_path, validate)
import bootstrap


def encoded(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def source_fingerprint(source: Path) -> str:
    files = [source / name for name in ('pyproject.toml', 'MANIFEST.in', 'README.md')]
    files += sorted(p for p in (source / 'src/tickets').rglob('*') if p.is_file()
                    and '__pycache__' not in p.parts and p.suffix != '.pyc')
    result = hashlib.sha256()
    for path in files:
        result.update(path.relative_to(source).as_posix().encode('utf-8'))
        result.update(b'\0' + path.read_bytes() + b'\0')
    return result.hexdigest()


def clean_environment() -> dict:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8', PIP_DISABLE_PIP_VERSION_CHECK='1')
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    return env


def command(argv: list[str | Path], *, timeout=600) -> subprocess.CompletedProcess:
    result = subprocess.run([str(a) for a in argv], capture_output=True, env=clean_environment(),
                            timeout=timeout, **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
    return result


def inspect_environment(environment: Path) -> dict:
    python = python_path(environment)
    if not python.is_file() or not (environment / 'pyvenv.cfg').is_file():
        raise ProjectError('The selected environment is missing or is not a virtual environment')
    probe = ('import sys,json,importlib.metadata as m,importlib.util; '
             'd=m.distribution("moldloft"); '
             'print(json.dumps(dict(prefix=sys.prefix,python=list(sys.version_info[:3]),'
             'version=d.version,direct_url=json.loads(d.read_text("direct_url.json") or "{}"),'
             'module=importlib.util.find_spec("tickets").origin)))')
    result = command([python, '-I', '-B', '-c', probe], timeout=30)
    if result.returncode:
        raise ProjectError('Moldloft is not importable in the selected environment')
    try:
        info = json.loads(result.stdout)
        if Path(info['prefix']).resolve() != environment.resolve() or info['python'][:2] < [3, 10]:
            raise ValueError()
    except (KeyError, TypeError, ValueError) as exc:
        raise ProjectError('The interpreter does not belong to the selected Python 3.10+ environment') from exc
    return info


def runtime_issues(project: Path, config: dict, receipt: dict | None) -> list[str]:
    issues = []
    source = resolve_path(project, config['source'])
    environment = resolve_path(project, config['environment'])
    try:
        info = inspect_environment(environment)
    except (ProjectError, OSError, subprocess.SubprocessError) as exc:
        return [str(exc) if isinstance(exc, ProjectError) else type(exc).__name__]
    if not receipt or receipt.get('status') != 'ready':
        issues.append('No completed installation receipt')
    else:
        for field, expected in {'project': str(project), 'source': str(source),
                                'environment': str(environment), 'mode': config['mode'],
                                'metadata_hash': digest((source / 'pyproject.toml').read_bytes())}.items():
            if receipt.get(field) != expected:
                issues.append(f'Installation {field} changed')
        if config['mode'] == 'wheel' and receipt.get('source_hash') != source_fingerprint(source):
            issues.append('Wheel source changed; reinstall to update the snapshot')
    direct = info['direct_url']
    editable = direct.get('dir_info', {}).get('editable') is True
    if config['mode'] == 'editable':
        location = urlparse(direct.get('url', ''))
        if (not editable or location.scheme != 'file' or
                Path(url2pathname(location.path)).resolve() != source or
                not Path(info['module']).resolve().is_relative_to(source / 'src')):
            issues.append('Editable installation points to a different source or uses a wheel')
    elif editable or not Path(info['module']).resolve().is_relative_to(environment):
        issues.append('Expected a wheel installed inside the selected environment')
    elif receipt and receipt.get('direct_url') != direct:
        issues.append('Installed wheel differs from its installation receipt')
    result = command([python_path(environment), '-I', '-B', '-m', 'pip', 'check'], timeout=30)
    if result.returncode:
        issues.append('Installed dependencies are inconsistent; rerun install')
    return issues


def managed_text(path: Path, project: Path, body: str | None, previous: dict,
                 *, comment: str = 'markdown') -> tuple[dict, str | None]:
    if not path.resolve().is_relative_to(project):
        raise ProjectError('Managed document resolves outside its project')
    raw = path.read_bytes() if path.exists() else b''
    try:
        text = raw.decode('utf-8')
    except UnicodeError as exc:
        raise ProjectError(f'Managed document is not UTF-8: {path}') from exc
    begin = '<!-- moldloft:project:begin -->' if comment == 'markdown' else '# moldloft:project:begin'
    end = '<!-- moldloft:project:end -->' if comment == 'markdown' else '# moldloft:project:end'
    key = portable_path(project, path)
    replacement = f'{begin}\n{body.rstrip()}\n{end}' if body else ''
    if begin in text or end in text:
        if text.count(begin) != 1 or text.count(end) != 1 or text.index(end) < text.index(begin):
            raise ProjectError(f'Malformed Moldloft document markers: {path}')
        start, stop = text.index(begin), text.index(end) + len(end)
        old = text[start:stop]
        if old != replacement and digest(old.encode('utf-8')) != previous.get(key):
            raise ProjectError(f'Managed document block was manually changed: {path}')
        updated = text[:start] + replacement + text[stop:]
    else:
        updated = text + ('\n\n' if text and not text.endswith('\n\n') else '') + replacement + ('\n' if replacement else '')
    return bootstrap.file_plan(path, project, updated.encode('utf-8'), label='document'), (digest(replacement.encode('utf-8')) if replacement else None)


def document_plans(project: Path, config: dict, receipt: dict | None) -> tuple[list, dict]:
    source = resolve_path(project, config['source'])
    rel = source.relative_to(project).as_posix()
    link = quote(rel, safe='/')
    body = (f'## Moldloft project entry\n\n'
            f'Moldloft source is in `{rel}/`. Read [the Moldloft README]({link}/README.md) for application usage.\n'
            f'Run `python "{rel}/tools/project.py" check --project .` to inspect the installation;\n'
            f'run `python "{rel}/tools/project.py" run --project .` to start the local application.\n'
            'Run these commands from this project root, or pass its absolute path.\n'
            f'Before changing Moldloft source, read [its development contract]({link}/AGENTS.md).\n'
            'Use the application API for live ticket edits; installation checks never open the database.\n')
    if config['principles']:
        body += (f'\nThis project adopts [the shared agent principles]({link}/docs/agent-principles.md).\n'
                 'Read and follow them in this project. Moldloft-specific development rules apply within its source.\n')
    previous = (receipt or {}).get('documents', {})
    if not isinstance(previous, dict):
        raise ProjectError('Invalid document ownership receipt; inspect it before reinstalling')
    plans, hashes = [], {}
    def add(relative, content, comment='markdown'):
        item, value = managed_text(project / relative, project, content, previous, comment=comment)
        if content is not None or (project / relative).exists():
            plans.append(item)
        if value:
            hashes[relative] = value
    add('AGENTS.md', body)
    add('CLAUDE.md', 'Read [AGENTS.md](AGENTS.md) for the Moldloft project entry and adopted principles.'
        if 'claude' in config['hosts'] else None)
    ignores = ['/.moldloft/*.local.json', '/.moldloft/.*.tmp',
               '/' + resolve_path(project, config['environment']).relative_to(project).as_posix() + '/']
    ignores += ['/' + bootstrap.HOST_FILES[h] for h in config['hosts']]
    ignores += ['/' + resolve_path(project, config['process_record']).relative_to(project).as_posix()]
    db = resolve_path(project, config['db'])
    if db.is_relative_to(project):
        ignores += ['/' + db.relative_to(project).as_posix() + '*',
                    '/' + (db.parent / 'drydocks').relative_to(project).as_posix() + '/**/*.db*']
    # Removing an adapter or staging a new environment must not expose the old
    # private settings, environment or database to version control. Preserve only
    # previously generated patterns whose ownership hash still matches.
    ignore_file = project / '.gitignore'
    if ignore_file.exists():
        text = ignore_file.read_bytes().decode('utf-8')
        begin, end = '# moldloft:project:begin', '# moldloft:project:end'
        if text.count(begin) == text.count(end) == 1 and text.index(begin) < text.index(end):
            old_block = text[text.index(begin):text.index(end) + len(end)]
            if digest(old_block.encode('utf-8')) == previous.get('.gitignore'):
                ignores += old_block.splitlines()[1:-1]
    add('.gitignore', '\n'.join(dict.fromkeys(ignores)), 'hash')
    return plans, hashes


def hook_plans(project: Path, config: dict, old_config: dict | None = None) -> list:
    source = resolve_path(project, config['source'])
    interpreter = python_path(resolve_path(project, config['environment']))
    items = []
    for host in config['hosts']:
        items.append(bootstrap.plan(host, source, interpreter))
        items.append(bootstrap.plan(host, source, interpreter, target_root=project,
                                    profile='shared' if config['principles'] else 'entry',
                                    installation_id=config['installation_id']))
    previous_hosts = set((old_config or {}).get('hosts', []))
    saved = read_json(owned_path(project, '.moldloft/hooks.local.json')) or {}
    for host in HOSTS:
        if isinstance(saved.get(host), dict) and saved[host].get(config['installation_id']):
            previous_hosts.add(host)
    for host in sorted(previous_hosts - set(config['hosts'])):
        items.append(bootstrap.plan(host, source, interpreter, target_root=project,
                                    installation_id=config['installation_id'], remove=True))
    return items


def configure(project: Path, options: argparse.Namespace) -> tuple[dict, dict | None]:
    old = read_json(owned_path(project, CONFIG))
    config = deepcopy(validate(project, old)) if old else defaults(project, SOURCE)
    config['source'] = portable_path(project, SOURCE)
    for name in ('environment', 'db', 'notes', 'process_record'):
        value = getattr(options, name, None)
        if value is not None:
            config[name] = portable_path(project, resolve_path(project, value))
    for name in ('mode', 'port', 'bind', 'start_path'):
        value = getattr(options, name, None)
        if value is not None:
            config[name] = value
    if options.host is not None:
        if 'none' in options.host and len(options.host) != 1:
            raise ProjectError('--host none cannot be combined with other hosts')
        config['hosts'] = [] if options.host == ['none'] else list(dict.fromkeys(options.host))
    if options.principles is not None:
        config['principles'] = options.principles == 'shared'
    if options.status_alias is not None:
        config['status_aliases'] = list(dict.fromkeys(options.status_alias))
    return validate(project, config), old


def check(project: Path, *, config: dict | None = None) -> dict:
    config = config or load(project)
    source = resolve_path(project, config['source'])
    if source != SOURCE:
        raise ProjectError('The manifest selects another source; rerun install from the intended checkout')
    bootstrap.validate_contract(source)
    receipt = read_json(owned_path(project, RECEIPT))
    if receipt and receipt.get('installation_id', config['installation_id']) != config['installation_id']:
        raise ProjectError('installation_id was changed; restore it before updating owned project configuration')
    issues = runtime_issues(project, config, receipt)
    plans = hook_plans(project, config)
    documents, _ = document_plans(project, config, receipt)
    issues += [f'{portable_path(project, item["path"])} is {item["status"]}' for item in plans + documents if item['changed']]
    return dict(status='ready' if not issues else 'stale', issues=issues,
                configurations=[dict(host=item['host'], path=str(item['path']), status=item['status'])
                                for item in plans],
                verification=(receipt or {}).get('verification', []) if (receipt or {}).get('status') == 'ready' else [],
                verification_scope='last_completed_installation',
                repair=f'python "{source / "tools/project.py"}" install --project "{project}"',
                live_host_activation='not_verified')


def install(project: Path, options: argparse.Namespace) -> dict:
    if not project.is_dir():
        raise ProjectError('The target project directory must already exist')
    config, old_config = configure(project, options)
    source, environment = resolve_path(project, config['source']), resolve_path(project, config['environment'])
    receipt_path = owned_path(project, RECEIPT)
    receipt = read_json(receipt_path)
    if receipt and receipt.get('installation_id', config['installation_id']) != config['installation_id']:
        raise ProjectError('installation_id was changed; restore it before updating owned project configuration')
    bootstrap.validate_contract(source)
    plans = hook_plans(project, config, old_config)
    docs, hashes = document_plans(project, config, receipt)
    plans += docs
    plans.append(bootstrap.file_plan(owned_path(project, CONFIG), project, encoded(config), label='manifest'))
    issues = runtime_issues(project, config, receipt)
    preserved_instance = (receipt or {}).get('preserved_instance')
    if issues:
        # Never replace files underneath a serving process. Its old templates and
        # static paths can remain in use after Python modules have been imported.
        from tickets.project_runtime import existing_instance
        try:
            active = existing_instance(project, config, config['port'], match_data=False)
        except ProjectError:
            active = None  # A foreign listener is diagnosed by run, not installation.
        if active:
            prior_environment = (preserved_instance or {}).get('environment') if (
                (preserved_instance or {}).get('instance') == active.get('instance')) else None
            active_environment = Path(active.get('environment') or prior_environment or
                                      resolve_path(project, (old_config or {}).get('environment', '.tools/moldloft'))).resolve()
            if environment == active_environment:
                raise ProjectError('A project instance is running. Stop it before updating this environment, '
                                   'or select a new --environment to prepare the next launch without interruption')
            preserved_instance = dict(instance=active.get('instance'), environment=str(active_environment))
        else:
            preserved_instance = None
    pending = dict(receipt or {}, status='installing', preserved_instance=preserved_instance)
    try:
        if issues:
            bootstrap.apply_plans([bootstrap.file_plan(receipt_path, project, encoded(pending))])
            if environment.exists() and not (environment / 'pyvenv.cfg').is_file():
                raise ProjectError('Refusing to replace an existing directory that is not a virtual environment')
            if not environment.exists():
                venv.EnvBuilder(with_pip=True).create(environment)
            python = python_path(environment)
            # Validate the environment's identity independently of app installation.
            probe = command([python, '-I', '-B', '-c', 'import sys; print(sys.prefix)'], timeout=30)
            if probe.returncode or Path(probe.stdout.decode().strip()).resolve() != environment:
                raise ProjectError('Virtual environment is broken or belongs to another path; rebuild it explicitly')
            if config['mode'] == 'editable':
                result = command([python, '-I', '-B', '-m', 'pip', 'install', '-e', source])
            else:
                with tempfile.TemporaryDirectory(prefix='moldloft-wheel-') as temporary:
                    build = command([python, '-I', '-B', '-m', 'pip', 'wheel', '--no-deps', '--wheel-dir', temporary, source])
                    wheels = list(Path(temporary).glob('moldloft-*.whl'))
                    if build.returncode or len(wheels) != 1:
                        raise ProjectError('Wheel build failed; no successful installation was recorded')
                    result = command([python, '-I', '-B', '-m', 'pip', 'install', '--force-reinstall', wheels[0]])
            if result.returncode:
                raise ProjectError(f'Package installation failed (exit {result.returncode}); rerun pip in the selected environment for diagnostics')
        info = inspect_environment(environment)
        completed = dict(status='ready', project=str(project), source=str(source), environment=str(environment),
                         installation_id=config['installation_id'],
                         mode=config['mode'], metadata_hash=digest((source / 'pyproject.toml').read_bytes()),
                         source_hash=source_fingerprint(source) if config['mode'] == 'wheel' else None,
                         direct_url=info['direct_url'], version=info['version'], documents=hashes,
                         preserved_instance=preserved_instance,
                         live_host_activation='not_verified')
        errors = runtime_issues(project, config, completed)
        if errors:
            raise ProjectError('; '.join(errors))
        verification = []
        for host in config['hosts']:
            verification.append(dict(bootstrap.self_test(host, source, python_path(environment)),
                                     target=str(source), profile='development'))
            profile = 'shared' if config['principles'] else 'entry'
            verification.append(dict(bootstrap.self_test(host, source, python_path(environment), target_root=project,
                                profile=profile, installation_id=config['installation_id']),
                                target=str(project), profile=profile))
        completed['verification'] = verification
        bootstrap.apply_plans(plans)
        bootstrap.apply_plans([bootstrap.file_plan(receipt_path, project, encoded(completed))])
        return dict(status='ready', mode=config['mode'], environment=str(environment), verification=verification,
                    live_host_activation='not_verified', activation='Review generated hooks in each selected host, then start/resume a session')
    except Exception:
        pending['status'] = 'failed'
        bootstrap.apply_plans([bootstrap.file_plan(receipt_path, project, encoded(pending))])
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    for action in ('install', 'check', 'run'):
        sub = actions.add_parser(action)
        sub.add_argument('--project', type=Path, required=True)
        if action in ('install', 'run'):
            sub.add_argument('--port', type=int)
        if action == 'install':
            sub.add_argument('--mode', choices=('editable', 'wheel'))
            sub.add_argument('--bind', help='IP address to listen on (default 127.0.0.1; no authentication)')
            sub.add_argument('--host', choices=(*HOSTS, 'none'), action='append')
            sub.add_argument('--principles', choices=('shared', 'none'))
            for flag in ('environment', 'db', 'notes', 'process-record', 'start-path'):
                sub.add_argument('--' + flag)
            sub.add_argument('--status-alias', action='append')
    options = parser.parse_args(argv)
    project = options.project.resolve()
    try:
        if options.action == 'install':
            result = install(project, options)
        else:
            result = check(project)
            if options.action == 'run' and result['status'] == 'ready':
                config = load(project)
                args = [str(python_path(resolve_path(project, config['environment']))), '-I', '-B', '-m',
                        'tickets', '--project', str(project)]
                if options.port is not None:
                    args += ['--port', str(options.port)]
                return subprocess.call(args, env=clean_environment())
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0 if result['status'] == 'ready' else 1
    except (ProjectError, bootstrap.BootstrapError, OSError, subprocess.SubprocessError) as exc:
        message = str(exc) if isinstance(exc, (ProjectError, bootstrap.BootstrapError)) else type(exc).__name__
        print(json.dumps(dict(status='error', error=message, live_host_activation='not_verified')))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
