"""Local, two-hour SQLite audit for the startup sanity process boundary.

This is disposable operational evidence, never a store for payloads or answers.
Run from the repository root: python -B tools/agent_hooks/sanity_audit.py --host codex
"""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time


RETENTION_SECONDS = 2 * 60 * 60
ROOT = Path(__file__).resolve().parents[2]
EVENTS = {"SessionStart", "SubagentStart", "Stop", "SubagentStop"}
HOSTS = {"claude", "codex", "zcode"}


def state_directory(root: Path | str = ROOT, *, host: str | None = None,
                    temporary_root: Path | str | None = None) -> Path:
    """Isolate independent checkouts and hosts without storing their raw identities."""
    checkout = os.path.normcase(str(Path(root).resolve()))
    digest = hashlib.sha256(checkout.encode("utf-8")).hexdigest()[:24]
    base = Path(temporary_root) if temporary_root is not None else Path(tempfile.gettempdir())
    return base / "moldloft-startup-sanity-v1" / digest / (host if host in HOSTS else "unknown")


DEFAULT_DB = state_directory() / "audit.sqlite3"
OUTCOMES = {
    "received", "challenge_issued", "passed", "retry_requested", "failed_open",
    "no_pending", "ignored", "error",
}


def identity(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _connect(path: Path | str) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=0.25)
    try:
        # Reuse free pages and erase deleted content without a growing WAL.
        connection.execute("PRAGMA secure_delete = ON")
        connection.execute("""CREATE TABLE IF NOT EXISTS audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at REAL NOT NULL,
            finished_at REAL,
            event TEXT,
            session_hash TEXT,
            scope_hash TEXT,
            challenge_id TEXT,
            question_id TEXT,
            outcome TEXT NOT NULL,
            error_type TEXT,
            host TEXT,
            llm TEXT,
            llm_source TEXT
        )""")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS audit_started_at ON audit_events(started_at)"
        )
        identity_columns = {"host", "llm", "llm_source"}
        columns = {row[1] for row in connection.execute("PRAGMA table_info(audit_events)")}
        if not identity_columns <= columns:
            # Recheck under a write lock: multiple hosts may open a v1 database
            # together. Never rebuild the table or invent identities for old rows.
            connection.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(audit_events)")}
            for column in sorted(identity_columns - columns):
                connection.execute(f"ALTER TABLE audit_events ADD COLUMN {column} TEXT")
            connection.commit()
        return connection
    except Exception:
        connection.close()
        raise


def _prune(connection: sqlite3.Connection, now: float) -> None:
    connection.execute(
        "DELETE FROM audit_events WHERE started_at < ?", (now - RETENTION_SECONDS,)
    )


def begin(path: Path | str, *, now: float | None = None, host: str | None = None) -> int:
    now = time.time() if now is None else now
    with closing(_connect(path)) as connection, connection:
        _prune(connection, now)
        cursor = connection.execute(
            "INSERT INTO audit_events(started_at, outcome, host) VALUES (?, 'received', ?)",
            (now, host if host in HOSTS else None),
        )
        return cursor.lastrowid


def finish(
    path: Path | str, row_id: int, record: dict[str, object], *, now: float | None = None
) -> None:
    now = time.time() if now is None else now
    if record.get("outcome") not in OUTCOMES:
        raise ValueError("unknown audit outcome")
    with closing(_connect(path)) as connection, connection:
        _prune(connection, now)
        connection.execute(
            """UPDATE audit_events SET finished_at=?, event=?, session_hash=?,
               scope_hash=?, challenge_id=?, question_id=?, outcome=?, error_type=?,
               host=?, llm=?, llm_source=?
               WHERE id=?""",
            (now, record.get("event"), record.get("session_hash"),
             record.get("scope_hash"), record.get("challenge_id"),
             record.get("question_id"), record["outcome"], record.get("error_type"),
             record.get("host"), record.get("llm"), record.get("llm_source"), row_id),
        )


def recent(
    path: Path | str = DEFAULT_DB, *, session: str | None = None, now: float | None = None
) -> list[dict[str, object]]:
    """Prune expired rows and return the active window without adding an audit row."""
    now = time.time() if now is None else now
    with closing(_connect(path)) as connection, connection:
        _prune(connection, now)
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(
            """SELECT *, datetime(started_at, 'unixepoch') AS started_utc
               FROM audit_events WHERE started_at >= ? AND started_at <= ?
               AND (? IS NULL OR session_hash = ?) ORDER BY started_at, id""",
            (now - RETENTION_SECONDS, now, session, identity(session) if session else None),
        )]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--host", choices=sorted(HOSTS))
    parser.add_argument("--session", help="Filter by session/thread ID; only its hash is stored")
    args = parser.parse_args(argv)
    args.db = args.db if args.db is not None else state_directory(host=args.host) / "audit.sqlite3"
    try:
        rows = recent(args.db, session=args.session)
    except (OSError, sqlite3.Error) as exc:
        print(f"Sanity audit unavailable: {type(exc).__name__}", file=sys.stderr)
        return 1
    print(json.dumps({"database": str(args.db), "retention_seconds": RETENTION_SECONDS,
                      "events": rows}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
