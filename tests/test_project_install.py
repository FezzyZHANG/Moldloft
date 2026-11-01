"""Project integration uses disposable directories; no live workspace or host trust."""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import project
import bootstrap
import startup_sanity as sanity
from tickets.project_config import CONFIG, RECEIPT, ProjectError, defaults, load, validate


def options(**values):
    args = dict(host=None, principles=None, status_alias=None, mode=None, port=None, bind=None,
                environment=None, db=None, notes=None, process_record=None, start_path=None)
    return argparse.Namespace(**(args | values))


def snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob('*') if p.is_file()}


class ProjectInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='moldloft-project-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name) / 'project'
        self.source = self.parent / "third party 'quoted' $cash-测试"
        self.source.mkdir(parents=True)
        for name in ('AGENTS.md', 'README.md', 'pyproject.toml', 'MANIFEST.in'):
            shutil.copyfile(ROOT / name, self.source / name)
        (self.source / 'docs').mkdir()
        shutil.copyfile(ROOT / 'docs/agent-principles.md', self.source / 'docs/agent-principles.md')
        shutil.copytree(ROOT / 'tools/agent_hooks', self.source / 'tools/agent_hooks',
                        ignore=shutil.ignore_patterns('__pycache__'))
        self.source_patch = patch.object(project, 'SOURCE', self.source)
        self.source_patch.start()
        self.addCleanup(self.source_patch.stop)

    def install_config(self, **kwargs):
        # Package execution is covered by the installed-distribution integration
        # check. Here a ready synthetic runtime isolates configuration ownership.
        with patch.object(project, 'runtime_issues', return_value=[]), \
             patch.object(project, 'inspect_environment', return_value={'version': 'fixture', 'direct_url': {}}), \
             patch.object(bootstrap, 'self_test', return_value={'synthetic_lifecycle': 'passed'}):
            return project.install(self.parent, options(**kwargs))

    def test_defaults_and_repeat_install_keep_exact_bytes_and_readonly_check(self):
        self.install_config()
        cfg = load(self.parent)
        self.assertEqual(cfg['mode'], 'editable')
        self.assertFalse(cfg['principles'])
        self.assertEqual(cfg['hosts'], [])
        self.assertEqual(cfg['source'], self.source.name)
        self.assertIn(f']({quote(self.source.name)}/README.md)',
                      (self.parent / 'AGENTS.md').read_text(encoding='utf-8'))
        before = snapshot(self.parent)
        self.install_config()
        self.assertEqual(snapshot(self.parent), before)
        with patch.object(project, 'runtime_issues', return_value=[]):
            self.assertEqual(project.check(self.parent)['status'], 'ready')
        self.assertEqual(snapshot(self.parent), before)
        self.assertFalse((self.parent / 'workspace').exists())

    def test_bind_is_written_only_when_selected_and_retained_on_reinstall(self):
        self.install_config()
        self.assertNotIn('bind', load(self.parent))
        self.install_config(bind='0.0.0.0')
        self.assertEqual(load(self.parent)['bind'], '0.0.0.0')
        self.install_config()
        self.assertEqual(load(self.parent)['bind'], '0.0.0.0')
        with self.assertRaisesRegex(ProjectError, 'bind'):
            self.install_config(bind='aglibot.local')
        self.assertEqual(load(self.parent)['bind'], '0.0.0.0')

    def test_selected_hosts_shared_principles_and_foreign_text_survive(self):
        (self.parent / 'AGENTS.md').write_bytes(b'# Existing instructions\r\nKeep this wording.\r\n')
        foreign = {'hooks': {'PreToolUse': [{'hooks': [{'type': 'command', 'command': 'foreign-tool'}]}]},
                   'operatorSetting': True}
        path = self.parent / '.codex/hooks.json'
        path.parent.mkdir()
        path.write_text(json.dumps(foreign), encoding='utf-8')
        self.install_config(host=['codex', 'claude', 'zcode'], principles='shared')
        cfg = load(self.parent)
        self.assertTrue(cfg['principles'])
        self.assertIn(b'# Existing instructions\r\nKeep this wording.\r\n', (self.parent / 'AGENTS.md').read_bytes())
        current = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(current['hooks']['PreToolUse'], foreign['hooks']['PreToolUse'])
        self.assertTrue(current['operatorSetting'])
        with patch.object(project, 'runtime_issues', return_value=[]), \
             patch.object(bootstrap, 'self_test', side_effect=AssertionError('check must not execute hooks')):
            checked = project.check(self.parent)
        self.assertEqual(len(checked['configurations']), 6)
        self.assertEqual(len(checked['verification']), 6)
        self.assertEqual({item['profile'] for item in checked['verification']}, {'development', 'shared'})
        self.assertEqual(checked['verification_scope'], 'last_completed_installation')
        self.assertEqual(checked['live_host_activation'], 'not_verified')
        before = snapshot(self.parent)
        self.install_config()
        self.assertEqual(before, snapshot(self.parent))
        self.install_config(principles='none', host=['codex'])
        self.assertNotIn('This project adopts', (self.parent / 'AGENTS.md').read_text(encoding='utf-8'))
        self.assertNotIn('moldloft:project:begin', (self.parent / 'CLAUDE.md').read_text(encoding='utf-8'))
        remaining = json.loads((self.parent / '.claude/settings.local.json').read_text(encoding='utf-8'))
        self.assertEqual(remaining['hooks'], {})
        hooks = json.loads(path.read_text(encoding='utf-8'))
        self.assertIn('--profile entry', hooks['hooks']['SessionStart'][0]['hooks'][0]['command'])

    def test_manual_document_change_is_reported_before_writes(self):
        self.install_config(host=['codex'])
        path = self.parent / 'AGENTS.md'
        original = path.read_bytes()
        path.write_bytes(original.replace(b'Moldloft project entry', b'My edited entry'))
        before = snapshot(self.parent)
        with self.assertRaisesRegex(ProjectError, 'manually changed'):
            self.install_config(principles='shared')
        self.assertEqual(before, snapshot(self.parent))

    def test_manifest_host_removal_retains_private_ignore_rules(self):
        self.install_config(host=['codex'])
        cfg = load(self.parent)
        cfg['hosts'] = []
        (self.parent / CONFIG).write_text(json.dumps(cfg), encoding='utf-8')
        with patch.object(project, 'runtime_issues', return_value=[]):
            self.assertEqual(project.check(self.parent)['status'], 'stale')
        self.install_config(environment='.tools/replacement')
        hooks = json.loads((self.parent / '.codex/hooks.json').read_text())
        self.assertEqual(hooks['hooks'], {})
        ignores = (self.parent / '.gitignore').read_text()
        self.assertIn('/.codex/hooks.json', ignores)
        self.assertIn('/.tools/moldloft/', ignores)
        self.assertIn('/.tools/replacement/', ignores)
        before = snapshot(self.parent)
        self.install_config()
        self.assertEqual(before, snapshot(self.parent))

    def test_manual_hook_change_is_reported_before_writes(self):
        self.install_config(host=['codex'])
        hooks = self.parent / '.codex/hooks.json'
        value = json.loads(hooks.read_text(encoding='utf-8'))
        value['hooks']['Stop'][0]['hooks'][0]['timeout'] = 77
        hooks.write_text(json.dumps(value), encoding='utf-8')
        before = snapshot(self.parent)
        with self.assertRaisesRegex(bootstrap.BootstrapError, 'manually changed'):
            self.install_config()
        self.assertEqual(before, snapshot(self.parent))

    def test_malformed_or_disabled_hook_stops_before_creating_installation(self):
        path = self.parent / '.codex/hooks.json'
        path.parent.mkdir()
        for content in ('{bad', '{"disableAllHooks":true}'):
            path.write_text(content, encoding='utf-8')
            before = snapshot(self.parent)
            with self.assertRaises(bootstrap.BootstrapError):
                self.install_config(host=['codex'])
            self.assertEqual(before, snapshot(self.parent))
            self.assertFalse((self.parent / CONFIG).exists())

    def test_cli_check_without_bytecode_flag_remains_readonly(self):
        self.install_config()
        package = self.source / 'src/tickets'
        package.mkdir(parents=True)
        for name in ('__init__.py', 'project_config.py'):
            shutil.copyfile(ROOT / 'src/tickets' / name, package / name)
        script = self.source / 'tools/project.py'
        shutil.copyfile(ROOT / 'tools/project.py', script)
        before = snapshot(self.parent)
        environment = dict(os.environ)
        environment.pop('PYTHONDONTWRITEBYTECODE', None)
        environment.pop('PYTHONPATH', None)
        result = subprocess.run([sys.executable, str(script), 'check', '--project', str(self.parent)],
                                cwd=self.temp.name, env=environment, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 1)  # This fixture intentionally has no runtime.
        self.assertEqual(json.loads(result.stdout)['status'], 'stale')
        self.assertEqual(before, snapshot(self.parent))

    def test_another_checkout_handler_is_not_removed_by_filename(self):
        other = self.parent / 'unrelated-source'
        foreign = bootstrap.definitions('claude', other, legacy=True)
        current = {'hooks': foreign}
        expected = bootstrap.definitions('claude', self.source)
        merged = bootstrap.merge_config(current, expected, 'claude', root=self.source)
        self.assertEqual(merged['hooks']['SessionStart'][0], foreign['SessionStart'][0])
        self.assertEqual(len(merged['hooks']['SessionStart']), 2)

    def test_relocation_uses_receipts_and_preserves_installation_id(self):
        self.install_config(host=['codex'])
        old_id = load(self.parent)['installation_id']
        moved = self.parent / 'moved-source'
        self.source.rename(moved)
        self.source = moved
        with patch.object(project, 'SOURCE', moved):
            self.install_config()
        self.assertEqual(load(self.parent)['source'], 'moved-source')
        self.assertEqual(load(self.parent)['installation_id'], old_id)
        handler = json.loads((self.parent / '.codex/hooks.json').read_text())['hooks']['Stop'][0]['hooks'][0]
        self.assertIn('moved-source', handler['command'])

    def test_path_escape_and_symlinked_configuration_are_rejected(self):
        cfg = defaults(self.parent, self.source)
        cfg['environment'] = '../outside'
        with self.assertRaises(ProjectError):
            validate(self.parent, cfg)
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        try:
            (self.parent / '.moldloft').symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('Creating a directory symlink requires host privileges')
        with self.assertRaises(ProjectError):
            self.install_config()
        self.assertEqual(list(outside.iterdir()), [])

    def test_failed_package_install_has_no_successful_manifest_or_receipt(self):
        def fake_environment(path):
            path.mkdir(parents=True)
            (path / 'pyvenv.cfg').write_text('fixture', encoding='utf-8')
        environment = self.parent / '.tools/moldloft'
        with patch.object(project, 'runtime_issues', return_value=['missing']), \
             patch('tickets.project_runtime.existing_instance', return_value=None), \
             patch.object(project.venv.EnvBuilder, 'create', side_effect=fake_environment), \
             patch.object(project, 'command', side_effect=[
                 subprocess.CompletedProcess([], 0, str(environment).encode(), b''),
                 subprocess.CompletedProcess([], 1, b'', b'fixture failure')]):
            with self.assertRaisesRegex(ProjectError, 'Package installation failed'):
                project.install(self.parent, options())
        self.assertFalse((self.parent / CONFIG).exists())
        self.assertEqual(json.loads((self.parent / RECEIPT).read_text())['status'], 'failed')

    def test_metadata_wrong_source_and_dependency_drift_are_reported(self):
        cfg = defaults(self.parent, self.source)
        info = {'direct_url': {'dir_info': {'editable': True}, 'url': (self.parent / 'wrong').as_uri()},
                'module': str(self.parent / 'wrong/src/tickets/__init__.py')}
        receipt = dict(status='ready', project=str(self.parent), source=str(self.source),
                       environment=str(self.parent / cfg['environment']), mode='editable', metadata_hash='old')
        with patch.object(project, 'inspect_environment', return_value=info), \
             patch.object(project, 'command', return_value=subprocess.CompletedProcess([], 1)):
            issues = project.runtime_issues(self.parent, cfg, receipt)
        self.assertTrue(any('metadata_hash' in i for i in issues))
        self.assertTrue(any('different source' in i for i in issues))
        self.assertTrue(any('dependencies' in i for i in issues))

    def test_active_environment_is_preserved_and_another_can_be_prepared(self):
        active = dict(project=str(self.parent), instance='serving-fixture', pid=123,
                      environment=str(self.parent / '.tools/moldloft'))
        before = snapshot(self.parent)
        with patch.object(project, 'runtime_issues', return_value=['missing']), \
             patch('tickets.project_runtime.existing_instance', return_value=active):
            with self.assertRaisesRegex(ProjectError, 'instance is running'):
                project.install(self.parent, options())
        self.assertEqual(before, snapshot(self.parent))
        prepared = self.parent / '.tools/prepared'
        def create_environment(path):
            path.mkdir(parents=True)
            (path / 'pyvenv.cfg').write_text('fixture', encoding='utf-8')
        with patch.object(project, 'runtime_issues', side_effect=[['missing'], []]), \
             patch('tickets.project_runtime.existing_instance', return_value=active), \
             patch.object(project.venv.EnvBuilder, 'create', side_effect=create_environment), \
             patch.object(project, 'inspect_environment', return_value={'version': 'fixture', 'direct_url': {}}), \
             patch.object(project, 'command', side_effect=[
                 subprocess.CompletedProcess([], 0, str(prepared).encode(), b''),
                 subprocess.CompletedProcess([], 0, b'', b'')]):
            project.install(self.parent, options(environment='.tools/prepared'))
        self.assertEqual(load(self.parent)['environment'], '.tools/prepared')
        saved = json.loads((self.parent / RECEIPT).read_text())
        self.assertEqual(saved['preserved_instance']['instance'], active['instance'])
        self.assertFalse((self.parent / 'workspace').exists())

    def test_generated_hooks_for_every_host_and_profile_run_offline(self):
        for host in project.HOSTS:
            for profile in ('shared', 'entry'):
                with self.subTest(host=host, profile=profile):
                    result = bootstrap.self_test(host, self.source, sys.executable, target_root=self.parent,
                                                 profile=profile, installation_id='fixture-parent')
                    self.assertEqual(result['synthetic_lifecycle'], 'passed')
                    self.assertEqual(result['live_host_activation'], 'not_verified')

    def test_parent_and_child_loading_yield_only_one_challenge(self):
        for cwd in (self.parent, self.source, self.source / 'src'):
            outputs = []
            for target, profile in ((self.parent, 'shared'), (self.source, 'development')):
                output = io.StringIO()
                sanity.main(io.StringIO(json.dumps(dict(hook_event_name='SessionStart', session_id='fixture', cwd=str(cwd)))),
                            output, state_dir=Path(self.temp.name) / ('state-' + profile),
                            project_root=target, source_root=self.source, profile=profile,
                            agents_path=self.source / 'AGENTS.md', host='codex')
                outputs.append(output.getvalue())
            self.assertEqual(sum('<moldloft-sanity>' in out for out in outputs), 1)
        parent = json.loads(outputs[1])['hookSpecificOutput']['additionalContext']
        self.assertIn('<moldloft-sanity>', parent)


if __name__ == '__main__':
    unittest.main()
