"""Explicit standalone configuration; never discover a host checkout or its secrets."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re


class ConfigurationError(ValueError):
    pass


def absolute_path(value, setting):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ConfigurationError(f"{setting} must be an absolute path")
    return path.resolve()


def load_env_file():
    """Only the explicitly named UTF-8 file fills absent process variables."""
    value = os.environ.get("TICKETS_ENV_FILE")
    if not value:
        return
    path = absolute_path(value, "TICKETS_ENV_FILE")
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as error:
        raise ConfigurationError("Cannot read TICKETS_ENV_FILE as UTF-8") from error
    values = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ConfigurationError("TICKETS_ENV_FILE requires KEY=VALUE lines")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    for key, value in values.items():
        os.environ.setdefault(key, value)


def enabled(setting):
    load_env_file()
    value = os.environ.get(setting, "off").lower()
    if value not in ("on", "off"):
        raise ConfigurationError(f"{setting} must be on or off")
    return value == "on"


def default_db_path():
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return absolute_path(base / "Moldloft" / "data" / "tickets.db", "LOCALAPPDATA")
    return Path.home() / ".local" / "share" / "moldloft" / "tickets.db"


def data_paths(db_path=None, notes_path=None):
    load_env_file()
    database = absolute_path(db_path or os.environ.get("TICKETS_DB") or default_db_path(), "TICKETS_DB")
    notebook = absolute_path(notes_path or os.environ.get("TICKETS_NOTES") or database.parent / "notes", "TICKETS_NOTES")
    return database, notebook


def repository_roots():
    load_env_file()
    value = os.environ.get("TICKETS_REPOSITORIES")
    if not value:
        return {}
    path = absolute_path(value, "TICKETS_REPOSITORIES")
    try:
        entries = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as error:
        raise ConfigurationError("TICKETS_REPOSITORIES must name a readable JSON object") from error
    if not isinstance(entries, dict):
        raise ConfigurationError("TICKETS_REPOSITORIES must contain an object")
    roots = {}
    for name, root in entries.items():
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name) or not isinstance(root, str):
            raise ConfigurationError("Repository entries require simple IDs and absolute paths")
        roots[name] = absolute_path(root, "Repository root")
    return roots
