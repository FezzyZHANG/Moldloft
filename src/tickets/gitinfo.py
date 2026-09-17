"""Read-only Git adapter with explicitly allowlisted repository roots and bounded text previews."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import re
import subprocess
import time

from .service import DomainError, LINK_FIELDS

from .config import repository_roots


class GitInfo:
    def __init__(self, roots=None):
        self.roots = {name: Path(path).resolve() for name, path in (repository_roots() if roots is None else roots).items()}
        self.cache = {}

    def run(self, repo, *args, limit=1_000_000):
        root = self.roots.get(repo) if isinstance(repo, str) else None
        if root is None:
            raise DomainError("Repository is not allowed")
        try:
            result = subprocess.run(["git", "--no-pager", "-c", "core.fsmonitor=false", "-C", str(root), *args],
                                    capture_output=True, timeout=10, check=False,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired) as error:
            raise DomainError("Git is unavailable or timed out", 503) from error
        if result.returncode:
            raise DomainError("Git reference or file is unavailable", 404)
        if len(result.stdout) > limit:
            raise DomainError("Git output is too large to preview")
        return result.stdout

    def resolve(self, repo, ref, kind="commit"):
        if not isinstance(ref, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}", ref):
            raise DomainError("Invalid Git reference")
        if kind == "branch":
            ref = ref if ref.startswith("refs/heads/") else "refs/heads/" + ref
        return self.run(repo, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}", limit=200).decode().strip()

    @staticmethod
    def safe_path(path):
        if not isinstance(path, str) or not path or len(path) > 1000:
            raise DomainError("A repository-relative file path is required")
        path = path.replace("\\", "/")
        parts = PurePosixPath(path).parts
        path = PurePosixPath(path).as_posix()
        lower = path.lower()
        if (path.startswith("/") or any(c in path for c in ":\0\r\n") or ".." in parts
                or any(p.lower() == ".git" or p.lower().startswith(".env") for p in parts)
                or lower.startswith(("os/history/", "os/chronicles/", "os/save/", "now/", "tickets/data/", "data/"))
                or lower == "auto/roster.md" or lower.endswith((".db", ".sqlite", ".sqlite3", ".pem", ".key"))
                or any(p.lower() in ("id_rsa", "id_ed25519") for p in parts)):
            raise DomainError("This path is outside the code preview boundary")
        return path

    def file(self, repo, sha, path, start=1, end=None):
        path = self.safe_path(path)
        entry = self.run(repo, "ls-tree", "-z", sha, "--", path)
        if not entry.startswith((b"100644 blob ", b"100755 blob ")):
            raise DomainError("Only tracked regular files can be previewed")
        size = int(self.run(repo, "cat-file", "-s", f"{sha}:{path}", limit=100))
        if size > 500_000:
            raise DomainError("File exceeds the 500 KB preview limit")
        data = self.run(repo, "cat-file", "blob", f"{sha}:{path}", limit=500_000)
        try:
            text = data.decode("utf-8")
        except UnicodeError as error:
            raise DomainError("Binary or non-UTF-8 file has no text preview") from error
        if "\0" in text:
            raise DomainError("Binary file has no text preview")
        lines = text.splitlines()
        if type(start) is not int or start < 1 or (end is not None and type(end) is not int):
            raise DomainError("Line range must use positive integers")
        if not lines and start == 1 and end in (None, 0):
            return {"path": path, "sha": sha, "start_line": 1, "end_line": 0, "total_lines": 0, "text": ""}
        end = min(start + 199, len(lines)) if end is None else end
        if end < start or end > len(lines) or end - start >= 500:
            raise DomainError("Line range is invalid or exceeds 500 lines")
        return {"path": path, "sha": sha, "start_line": start, "end_line": end,
                "total_lines": len(lines), "text": "\n".join(lines[start - 1:end])}

    def normalize_link(self, value):
        if not isinstance(value, dict) or set(value) - LINK_FIELDS:
            raise DomainError("Invalid code link")
        repo, kind, ref = value.get("repo"), value.get("kind"), value.get("ref")
        if kind not in ("branch", "commit", "file"):
            raise DomainError("Invalid code link kind")
        sha = self.resolve(repo, ref, kind)
        result = {"repo": repo, "kind": kind, "ref": ref if kind == "branch" else sha,
                  "path": "", "start_line": None, "end_line": None}
        if kind == "file":
            start = value.get("start_line")
            preview = self.file(repo, sha, value.get("path"), 1 if start is None else start, value.get("end_line"))
            result.update({k: preview[k] for k in ("path", "start_line", "end_line")})
        return result

    def inspect(self, link):
        link = self.normalize_link({k: link.get(k) for k in LINK_FIELDS})
        repo, kind = link["repo"], link["kind"]
        sha = self.resolve(repo, link["ref"], kind)
        summary = self.run(repo, "show", "-s", "--format=%H%n%cs%n%s", sha, limit=20000).decode("utf-8", "replace")
        result = {"sha": sha, "summary": summary, "kind": kind}
        if kind == "file":
            result.update(self.file(repo, sha, link["path"], link["start_line"], link["end_line"]))
            result["working_changes"] = self.run(repo, "status", "--porcelain=v1", "--untracked-files=no", "--", link["path"], limit=10000).decode("utf-8", "replace")
        else:
            names = self.run(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", "-z", sha).decode("utf-8", "replace").split("\0")
            files = []
            for name in names:
                try:
                    if name:
                        files.append(self.safe_path(name))
                except DomainError:
                    pass
            result["files"] = files[:200]
        return result

    def refs(self, repo):
        # A short TTL also notices branch and working-tree changes without a HEAD move.
        cached = self.cache.get(repo)
        if cached and time.monotonic() - cached[0] < 3:
            return cached[1]
        head = self.resolve(repo, "HEAD")
        branches = self.run(repo, "for-each-ref", "--count=100", "--format=%(refname:short)", "refs/heads").decode("utf-8", "replace").splitlines()
        commits = self.run(repo, "log", "-50", "--format=%H%x09%cs%x09%s", "--no-decorate").decode("utf-8", "replace").splitlines()
        result = {"repo": repo, "head": head, "branches": branches, "commits": commits}
        self.cache[repo] = time.monotonic(), result
        return result
