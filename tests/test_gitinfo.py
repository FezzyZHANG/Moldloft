"""Git previews use an isolated repository and never mutate the real checkout."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from tickets.gitinfo import GitInfo
from tickets.service import DomainError


class GitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        (self.root / 'code.py').write_text('first\nsecond\nthird\n', encoding='utf-8')
        (self.root / 'binary.dat').write_bytes(b'\0binary')
        (self.root / '.env').write_text('SYNTHETIC=not-a-real-secret', encoding='utf-8')
        (self.root / 'os/history').mkdir(parents=True)
        (self.root / 'os/history/fixture.md').write_text('Synthetic history', encoding='utf-8')
        self.git('add', '.')
        self.git('commit', '-m', 'Fixture')
        self.info = GitInfo({'tickets': self.root})

    def tearDown(self):
        # Git object files are read-only on Windows. TemporaryDirectory's cleanup
        # handles that itself; no shell deletion or real repository is involved.
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], check=True,
                              capture_output=True).stdout.decode().strip()

    def test_file_is_pinned_and_branch_remains_live(self):
        file = self.info.normalize_link({'repo': 'tickets', 'kind': 'file', 'ref': 'HEAD',
                                        'path': 'code.py', 'start_line': 2, 'end_line': 3})
        branch = self.info.normalize_link({'repo': 'tickets', 'kind': 'branch', 'ref': 'main'})
        old_sha = file['ref']
        (self.root / 'code.py').write_text('changed\nnew\nthird\n', encoding='utf-8')
        self.assertTrue(self.info.inspect(file)['working_changes'])
        self.git('add', 'code.py'); self.git('commit', '-m', 'Next fixture')
        self.assertEqual(self.info.inspect(file)['text'], 'second\nthird')
        self.assertNotEqual(self.info.inspect(branch)['sha'], old_sha)
        self.assertNotIn('os/history/fixture.md', self.info.inspect({'repo': 'tickets', 'kind': 'commit', 'ref': old_sha})['files'])

    def test_sensitive_paths_binary_ranges_and_missing_refs(self):
        for path in ('.env', './.env', 'os/./history/fixture.md', './os/history/fixture.md',
                     '../code.py', 'C:/code.py', 'os\\history\\fixture.md'):
            with self.subTest(path=path), self.assertRaises(DomainError):
                self.info.normalize_link({'repo': 'tickets', 'kind': 'file', 'ref': 'HEAD', 'path': path})
        for path, start, end in [('binary.dat', 1, None), ('code.py', 0, None), ('code.py', True, None),
                                 ('code.py', 1, True), ('code.py', 2, 5)]:
            with self.subTest(path=path, start=start, end=end), self.assertRaises(DomainError):
                self.info.normalize_link({'repo': 'tickets', 'kind': 'file', 'ref': 'HEAD',
                                          'path': path, 'start_line': start, 'end_line': end})
        for repo, ref in [('elsewhere', 'HEAD'), ('tickets', '--help'), ('tickets', 'absent')]:
            with self.assertRaises(DomainError):
                self.info.resolve(repo, ref)


if __name__ == '__main__':
    unittest.main()
