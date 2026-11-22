"""Isolated fixtures shared by the graph, Git, and proposal tests."""

from pathlib import Path
import os
import subprocess
from tempfile import TemporaryDirectory
from unittest import TestCase

from moldloft.store import Store


class StoreCase(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = Store(self.root / "db" / "moldloft.db")

    def ticket(self, title, **fields):
        op = dict(op="ticket_create", temp_id="new:t", fields=dict(title=title, **fields))
        return self.store.mutate([op])["created"]["new:t"]

    def note(self, key, **fields):
        self.store.mutate([dict(op="note_create", id=key, fields=dict(title=key, **fields))])
        return key

    def git(self, *args, data=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        result = subprocess.run(["git", "-C", str(self.repo), *args], input=data, capture_output=True, check=True, env=env)
        return result.stdout.decode().strip()

    def checkout(self):
        self.repo = self.root / "checkout"
        self.repo.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.autocrlf", "false")
        self.git("config", "core.hooksPath", str(self.root / "unused-hooks"))
        self.file = self.repo / "read me.txt"
        self.file.write_bytes(b"alpha\nbravo\ncharlie\ndelta\n")
        self.commit("initial")

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-m", message)
        return self.git("rev-parse", "HEAD")
