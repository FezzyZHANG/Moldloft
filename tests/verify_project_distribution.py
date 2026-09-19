"""Opt-in package integration check; dependency installation may use the network.

Only allowlisted source and disposable application fixtures enter the temporary
project. No live workspace, host settings or source checkout Git state is copied.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import urllib.error
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def run(args, cwd):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8', PIP_DISABLE_PIP_VERSION_CHECK='1')
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    result = subprocess.run([str(a) for a in args], cwd=cwd, env=env, capture_output=True, timeout=600,
                            **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
    if result.returncode:
        raise RuntimeError(f'Fixture command failed: {args[:4]}\n' +
                           result.stdout.decode('utf-8', errors='replace')[-2000:] +
                           result.stderr.decode('utf-8', errors='replace')[-2000:])
    return result.stdout.decode('utf-8')


def python(environment):
    return environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def verify_cli(interpreter, installer, parent, elsewhere):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    prior_id = None
    for attempt in range(2):
        with (elsewhere / 'server.log').open('wb') as log:
            process = subprocess.Popen([str(interpreter), '-I', '-B', '-m', 'tickets', '--project', str(parent),
                                        '--port', str(port)], cwd=elsewhere, stdout=log, stderr=log,
                **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
            try:
                deadline = time.monotonic() + 20
                while True:
                    if process.poll() is not None:
                        raise RuntimeError('Disposable project CLI exited before readiness')
                    try:
                        with opener.open(f'http://127.0.0.1:{port}/api/instance', timeout=1) as response:
                            identity = json.load(response)
                        break
                    except (urllib.error.URLError, TimeoutError):
                        if time.monotonic() >= deadline:
                            raise RuntimeError('Disposable project CLI did not become ready')
                        time.sleep(0.05)
                # Windows venv's executable is a redirector with a child Python
                # process. Validate the fixture identity rather than the shim PID.
                assert identity['project'] == str(parent)
                assert Path(identity['environment']).resolve() == interpreter.parent.parent.resolve()
                assert identity['instance'] != prior_id
                prior_id = identity['instance']
                reused = run([sys.executable, '-B', installer, 'run', '--project', parent, '--port', str(port)], elsewhere)
                assert 'already running' in reused
                with opener.open(f'http://127.0.0.1:{port}/api/graph', timeout=2) as response:
                    assert json.load(response)['tickets'][0]['title'] == 'Distribution fixture'
            finally:
                # Only the Popen-owned fixture is stopped. On Windows this also
                # exercises recovery from a stale process receipt after abrupt exit.
                if process.poll() is None:
                    if os.name == 'nt':
                        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                       capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
                    else:
                        process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='moldloft-distribution-') as directory:
        scratch = Path(directory).resolve()
        parent = scratch / 'parent without git'
        source = parent / "vendor 'quote' $cash 测试"
        source.mkdir(parents=True)
        elsewhere = scratch / 'unrelated'
        elsewhere.mkdir()
        for name in ('pyproject.toml', 'MANIFEST.in', 'README.md', 'AGENTS.md', 'CLAUDE.md',
                     'CHANGELOG.md', 'DESIGN.md', 'DEPLOYMENT.md', 'MCP.md', '.env.example',
                     '.gitignore', '.gitattributes'):
            shutil.copyfile(ROOT / name, source / name)
        for folder in ('src', 'tools', 'tests', 'docs'):
            shutil.copytree(ROOT / folder, source / folder,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.egg-info'))
        (source / 'config').mkdir()
        for path in (ROOT / 'config').glob('*.example.json'):
            shutil.copyfile(path, source / 'config' / path.name)
        smoke = elsewhere / 'smoke.py'
        shutil.copyfile(ROOT / 'tests/project_distribution_smoke.py', smoke)
        installer = source / 'tools/project.py'
        reports = {}
        for mode in ('editable', 'wheel'):
            print(f'Verifying {mode} installation in a temporary project...', flush=True)
            result = json.loads(run([sys.executable, '-B', installer, 'install', '--project', parent,
                        '--mode', mode, '--environment', '.tools/' + mode,
                        '--host', 'codex', '--principles', 'shared'], elsewhere))
            assert result['status'] == 'ready', result
            interpreter = python(parent / '.tools' / mode)
            manifest = parent / '.moldloft/project.json'
            before = manifest.read_bytes(), manifest.stat().st_mtime_ns
            check = json.loads(run([sys.executable, '-B', installer, 'check', '--project', parent], elsewhere))
            assert check['status'] == 'ready', check
            assert before == (manifest.read_bytes(), manifest.stat().st_mtime_ns)
            reports[mode] = json.loads(run([interpreter, '-I', '-B', smoke, '--project', parent, '--mode', mode], elsewhere))
            verify_cli(interpreter, installer, parent, elsewhere)
            reports[mode]['cli_reuse_restart'] = 'passed'
            repeated = json.loads(run([sys.executable, '-B', installer, 'install', '--project', parent], elsewhere))
            assert repeated['status'] == 'ready'
            assert before == (manifest.read_bytes(), manifest.stat().st_mtime_ns)
            if mode == 'wheel':
                hidden = parent / 'source-unavailable'
                # Both move targets are verified within this disposable fixture.
                assert source.resolve().is_relative_to(scratch) and hidden.resolve().is_relative_to(scratch)
                source.rename(hidden)
                try:
                    run([interpreter, '-I', '-B', smoke, '--project', parent, '--mode', mode], elsewhere)
                finally:
                    hidden.rename(source)
        print('Converting the installed wheel environment to editable...', flush=True)
        converted = json.loads(run([sys.executable, '-B', installer, 'install', '--project', parent,
                                    '--mode', 'editable'], elsewhere))
        assert converted['status'] == 'ready' and converted['mode'] == 'editable'
        assert Path(converted['environment']) == parent / '.tools/wheel'
        check = json.loads(run([sys.executable, '-B', installer, 'check', '--project', parent], elsewhere))
        assert check['status'] == 'ready', check
        assert len(check['configurations']) == len(check['verification']) == 2
        reports['wheel_to_editable'] = json.loads(run([interpreter, '-I', '-B', smoke, '--project', parent,
                                                      '--mode', 'editable'], elsewhere))
        print('Building and inspecting wheel and source archives...', flush=True)
        run([interpreter, '-I', '-B', '-m', 'pip', 'install', 'build>=1.2,<2'], elsewhere)
        dist = scratch / 'dist'
        run([interpreter, '-I', '-B', '-m', 'build', '--outdir', dist, source], elsewhere)
        with zipfile.ZipFile(next(dist.glob('*.whl'))) as archive:
            wheel = archive.namelist()
        with tarfile.open(next(dist.glob('*.tar.gz'))) as archive:
            sdist = [name.split('/', 1)[1] for name in archive.getnames() if '/' in name]
        assert all(p in wheel for p in ('tickets/project_config.py', 'tickets/project_runtime.py',
                                       'tickets/static/vendor/elk/elk-worker.min.js'))
        assert all(p in sdist for p in ('tools/project.py', 'docs/agent-principles.md',
                                       'tests/test_project_install.py', 'tools/agent_hooks/startup_sanity.py'))
        assert not any(p.startswith(('tools/', 'docs/')) for p in wheel)
        for inventory in (wheel, sdist):
            assert not any(p.startswith(('.codex/', '.claude/', '.zcode/', '.moldloft/', 'workspace/', 'notes/', 'review/'))
                           or p.endswith(('.db', '.sqlite3')) for p in inventory)
        reports['archives'] = dict(wheel_members=len(wheel), sdist_members=len(sdist), private_state='excluded')
        reports['live_host_activation'] = 'not_verified'
    report = json.dumps(reports, indent=2) + '\n'
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report, encoding='utf-8')
    print(report)


if __name__ == '__main__':
    main()
