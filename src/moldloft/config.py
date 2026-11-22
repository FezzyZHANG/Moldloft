"""Explicit process, database, and checkout configuration."""

import json
import os
from pathlib import Path
import re

from .errors import DomainError


def load_env_file(path=None):
    path = path or os.environ.get("MOLDLOFT_ENV_FILE")
    if not path:
        return
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise DomainError(f"Invalid environment assignment on line {number}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def default_db_path() -> Path:
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
        return Path(root) / "Moldloft" / "moldloft.db"
    return Path.home() / ".local" / "share" / "moldloft" / "moldloft.db"


def resolve_db_path(explicit=None) -> Path:
    return Path(explicit or os.environ.get("MOLDLOFT_DB") or default_db_path()).expanduser().resolve()


def resolve_repos(explicit=None, repos_file=None) -> dict[str, Path]:
    path = repos_file or os.environ.get("MOLDLOFT_REPOS")
    repos = {}
    if path:
        try:
            repos = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DomainError(f"Cannot read repository configuration: {exc}") from exc
    if not isinstance(repos, dict) or (explicit is not None and not isinstance(explicit, dict)):
        raise DomainError("Repository configuration must be an object")
    roots = {}
    for name, root in (repos | (explicit or {})).items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,39}", name):
            raise DomainError("Invalid repository ID")
        if not isinstance(root, (str, Path)) or not Path(root).is_absolute():
            raise DomainError(f"Repository {name} requires an absolute path")
        roots[name] = Path(root).resolve()
    return roots
