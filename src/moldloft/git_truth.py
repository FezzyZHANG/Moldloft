"""Pin and compare Git objects without changing the checkout or the saved anchor."""

from collections import OrderedDict
from copy import deepcopy
from difflib import unified_diff
from hashlib import sha256
from itertools import islice
import os
import re
import subprocess
from threading import Lock
from time import monotonic

from .config import RepoConfig
from .errors import DomainError

_REF = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._/-]{0,254}\Z")
_MENTION = re.compile(r"`([^`\r\n]+)`|\]\(<?([^()<>\r\n]+?)>?\)|([\w./\\:@~-]+)")


def _path_mentions(text):
    mentions = {}
    for token in _MENTION.finditer(text):
        value = token[1] or token[2] or token[3]
        match = re.fullmatch(r"([^:]+)(?::([0-9]{1,9})(?:-([0-9]{1,9}))?)?", value.rstrip("."))
        if not match:
            continue
        path = match[1].removeprefix("./")
        start = int(match[2]) if match[2] else None
        end = int(match[3]) if match[3] else start
        if start is None or 1 <= start <= end < start + 500:
            mentions.setdefault(path, []).append((start, end))
    return mentions


def safe_path(path):
    if not isinstance(path, str) or len(path) > 1000 or any(c in path for c in ":\\\0\r\n"):
        raise DomainError("Invalid anchor path")
    for part in path.lower().split("/"):
        if (part in {"", ".", "..", ".git", "id_rsa", "id_ed25519"}
                or part.startswith(".env") or part.endswith((".pem", ".key"))):
            raise DomainError("Invalid or restricted anchor path")
    return path


def _ref(value):
    if (not isinstance(value, str) or not _REF.fullmatch(value) or ".." in value
            or value.endswith(".") or any(not p or p.startswith(".") or p.endswith(".lock")
                                          for p in value.split("/"))):
        raise DomainError("Invalid Git ref")
    return value


def _run(root, *args, allowed=(0,)):
    # Ignore inherited Git location overrides and disable optional index writes from status.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_LITERAL_PATHSPECS="1", GIT_NO_LAZY_FETCH="1",
               GIT_CEILING_DIRECTORIES=str(root.parent))
    try:
        result = subprocess.run(
            ["git", "--no-pager", "-c", "core.fsmonitor=false", "-C", str(root), *args],
            capture_output=True, timeout=10, check=False, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DomainError("Git checkout is unreachable") from exc
    if result.returncode not in allowed:
        raise DomainError(result.stderr.decode("utf-8", "replace").strip()[:500] or "Git object missing")
    return result


def _text(root, *args):
    return _run(root, *args).stdout.decode("utf-8", "replace").rstrip("\r\n")


def _sha(root, ref):
    return _text(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")


def _subject(root, sha):
    return _text(root, "show", "-s", "--format=%cs %s", sha)[:4000]


def _digest(lines):
    return sha256("".join(lines).encode("utf-8")).hexdigest()


def anchor_spec(spec):
    """Validate authored fields without consulting a checkout."""
    if not isinstance(spec, dict) or not isinstance(spec.get("repo"), str) or not spec["repo"]:
        raise DomainError("Anchor needs a repository")
    kind = spec.get("kind")
    if kind not in ("file", "commit", "branch"):
        raise DomainError("Invalid anchor kind")
    ref = _ref(spec.get("ref", None if kind == "branch" else "HEAD"))
    if kind == "branch":
        ref = _ref(ref.removeprefix("refs/heads/"))
    path = spec.get("path", "")
    start, end = spec.get("start_line"), spec.get("end_line")
    if kind == "file":
        path = safe_path(path)
        start = 1 if start is None else start
        if type(start) is not int or start < 1:
            raise DomainError("Invalid start line")
        if end is not None and (type(end) is not int or not start <= end < start + 500):
            raise DomainError("Invalid end line or excerpt exceeds 500 lines")
    elif path != "" or start is not None or end is not None:
        raise DomainError("Only file anchors have paths and line ranges")
    symbol, claim = spec.get("symbol", ""), spec.get("claim", "")
    if not isinstance(symbol, str) or len(symbol) > 160 or not isinstance(claim, str) or len(claim) > 500:
        raise DomainError("Invalid anchor symbol or claim")
    return dict(repo=spec["repo"], kind=kind, ref=ref, path=path, start_line=start,
                end_line=end, symbol=symbol, claim=claim)


def _compare_span(anchor, old, lines, head):
    start, end = anchor["start_line"], anchor["end_line"]
    current = lines[start - 1:end]
    if end <= len(lines) and _digest(current) == anchor["content_hash"]:
        return dict(status="fresh", current_range=[start, end])
    original = old[start - 1:end]
    size = len(original)
    if size and _digest(original) == anchor["content_hash"]:
        for i in range(len(lines) - size + 1):
            if lines[i] == original[0] and lines[i:i + size] == original:
                return dict(status="moved", current_range=[i + 1, i + size], message="Anchored lines moved",
                            suggested_pin=dict(ref=head, start_line=i + 1, end_line=i + size))
    path = anchor["path"]
    diff = unified_diff(original, current, f"{anchor['pinned_sha'][:12]}:{path}", f"{head[:12]}:{path}")
    def display():
        for line in diff:
            text = line.rstrip("\n")
            yield text if len(text) <= 500 else text[:499] + "…"
            if not line.endswith("\n"):
                yield r"\ No newline at end of file"
    return dict(status="drifted", message="Anchored lines changed", diff="\n".join(islice(display(), 120)))


class GitTruth:
    def __init__(self, repos=None, excludes: dict[str, tuple[str, ...]] | None = None):
        self.roots = {}
        self.excludes = {}
        for name, entry in (repos or {}).items():
            config = RepoConfig.parse(name, entry, (excludes or {}).get(name, ()))
            self.roots[name] = config.path
            self.excludes[name] = tuple(rule.casefold() for rule in config.exclude)
        self._cache = OrderedDict()
        self._lock = Lock()

    def _cached(self, key, read):
        with self._lock:
            entry = self._cache.get(key)
            if entry and monotonic() < entry[0]:
                return entry[1]
        value = read()
        with self._lock:
            self._cache[key] = (monotonic() + 3, value)
            self._cache.move_to_end(key)
            if len(self._cache) > 64:
                self._cache.popitem(last=False)
        return value

    def _root(self, repo):
        if not isinstance(repo, str) or repo not in self.roots:
            raise DomainError("Unknown repository")
        root = self.roots[repo]
        if not (root / ".git").exists():
            raise DomainError("Configured Git checkout is unreachable")
        return root

    def _check_path(self, repo, path):
        folded = safe_path(path).casefold()
        for rule in self.excludes[repo]:
            if folded == rule or folded.startswith(rule.removesuffix("/") + "/"):
                raise DomainError("Anchor path excluded by repository configuration")
        return path

    def suggest_paths(self, text: str, *, anchored=()) -> list[dict]:
        mentions = _path_mentions(text)
        suggestions = []
        for repo in self.roots:
            try:
                root = self._root(repo)
                head = _sha(root, "HEAD")
                # Check the top-level tree once so ordinary prose costs no per-word Git reads.
                names = self._cached(("tree", root, head), lambda: set(
                    _run(root, "ls-tree", "-z", "--name-only", head).stdout.split(b"\0")[:-1]))
            except DomainError:
                continue
            for path, spans in mentions.items():
                if (repo, path) in anchored or path.partition("/")[0].encode("utf-8") not in names:
                    continue
                try:
                    self._check_path(repo, path)
                    lines = self._lines(root, head, path)
                except DomainError:
                    continue
                for start, end in spans:
                    if end is None or end <= len(lines):
                        suggestions.append(dict(repo=repo, path=path, start_line=start, end_line=end))
                        break
                if len(suggestions) == 20:
                    return suggestions
        return suggestions

    def _lines(self, root, sha, path):
        def read():
            entry = _run(root, "ls-tree", "-lz", sha, "--", path).stdout
            header, _, name = entry.partition(b"\t")
            if (not header.startswith((b"100644 blob ", b"100755 blob "))
                    or name != path.encode("utf-8") + b"\0"):
                raise DomainError("Tracked regular file missing at this ref")
            oid = header.split()[2].decode("ascii")
            if int(header.split()[3]) > 500_000:
                raise DomainError("Anchor file exceeds 500 KB")
            blob = _run(root, "cat-file", "blob", oid).stdout
            if b"\0" in blob:
                raise DomainError("Anchor file contains NUL bytes")
            try:
                return blob.decode("utf-8").splitlines(keepends=True) or [""]
            except UnicodeDecodeError as exc:
                raise DomainError("Anchor file must be UTF-8") from exc
        return self._cached(("lines", root, sha, path), read)

    def pin(self, spec: dict) -> dict:
        fields = anchor_spec(spec)
        root = self._root(fields["repo"])
        kind, ref = fields["kind"], fields["ref"]
        if kind == "file":
            self._check_path(fields["repo"], fields["path"])
        sha = _sha(root, f"refs/heads/{ref}" if kind == "branch" else ref)
        if kind == "file":
            lines = self._lines(root, sha, fields["path"])
            start = fields["start_line"]
            end = fields["end_line"] or min(len(lines), start + 199)
            if not start <= end <= len(lines):
                raise DomainError("Line range exceeds file length")
            fields["end_line"] = end
            span = lines[start - 1:end]
            content_hash, excerpt = _digest(span), "".join(span)[:4000]
        else:
            content_hash, excerpt = sha, _subject(root, sha)
        return dict(fields, ref=ref if kind == "branch" else sha, pinned_sha=sha,
                    content_hash=content_hash, excerpt=excerpt)

    def verify(self, anchor: dict) -> dict:
        return self._verify(anchor, {})

    def verify_many(self, anchors: list[dict]) -> list[dict]:
        reads = {}
        return [self._verify(anchor, reads) for anchor in anchors]

    def _verify(self, anchor, reads):
        repo, kind, pinned = anchor["repo"], anchor["kind"], anchor["pinned_sha"]
        result = dict(anchor_id=anchor.get("id"), target_type=anchor.get("target_type"),
                      target_id=anchor.get("target_id"), repo=repo, kind=kind, status="missing",
                      head_sha=None, pinned_sha=pinned, current_range=None, diff="",
                      working_dirty=False, message="", suggested_pin=None)
        try:
            root = self._root(repo)
            path = self._check_path(repo, anchor["path"]) if kind == "file" else ""
            ref = "refs/heads/" + _ref(anchor["ref"]) if kind == "branch" else "HEAD"
            if (root, ref) not in reads:
                reads[root, ref] = _sha(root, ref)
            head = result["head_sha"] = reads[root, ref]
            key = (root, head, path)
            if key not in reads:
                args = ("--", path) if path else ()
                reads[key] = bool(_run(root, "status", "--porcelain=v1", "--untracked-files=no", *args).stdout)
            dirty = result["working_dirty"] = reads[key]
            _ref(pinned)
            if kind == "file":
                old = self._lines(root, pinned, path)
                lines = self._lines(root, head, path)
                result.update(_compare_span(anchor, old, lines, head))
                if result["status"] != "fresh":
                    return result
            elif kind == "branch" and pinned != head:
                count = _text(root, "rev-list", "--count", f"{pinned}...{head}")
                return dict(result, status="drifted", message=f"Branch moved ({count} commits): {_subject(root, head)}")
            elif kind == "commit":
                if _run(root, "merge-base", "--is-ancestor", pinned, head, allowed=(0, 1)).returncode:
                    return dict(result, status="drifted", message="Pinned commit is no longer an ancestor of HEAD")
            return dict(result, status="dirty" if dirty else "fresh",
                        message="Tracked working tree changes" if dirty else "Pin verified")
        except DomainError as exc:
            return dict(result, message=exc.message)

    def refs(self, repo: str) -> dict:
        root = self._root(repo)

        def read():
            head = _sha(root, "HEAD")
            branches = _text(root, "for-each-ref", "--format=%(refname:strip=2)", "refs/heads").splitlines()
            log = _text(root, "log", "-50", "--format=%H%x09%cs%x09%s", head)
            commits = [dict(zip(("sha", "date", "subject"), line.split("\t", 2))) for line in log.splitlines()]
            return dict(repo=repo, head=head, branches=branches, commits=commits)
        return deepcopy(self._cached(("refs", root, repo), read))
