"""SQLite schema and transaction primitives."""

from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from .errors import DomainError

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE graphs (
    id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT, graph_id TEXT NOT NULL REFERENCES graphs(id) ON DELETE CASCADE,
    title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', tech_notes TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open', priority TEXT NOT NULL DEFAULT 'medium', group_name TEXT NOT NULL DEFAULT '',
    parent_id INTEGER REFERENCES tickets(id) ON DELETE SET NULL, version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE ticket_edges (
    blocker_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    blocked_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    PRIMARY KEY (blocker_id, blocked_id), CHECK (blocker_id != blocked_id)
);
CREATE TABLE notes (
    id TEXT PRIMARY KEY, graph_id TEXT NOT NULL REFERENCES graphs(id) ON DELETE CASCADE,
    title TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '', in_graph INTEGER NOT NULL DEFAULT 1, adopted_body_hash TEXT,
    version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE note_links (
    source_note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE, relation TEXT NOT NULL,
    target_type TEXT NOT NULL, target_id TEXT NOT NULL,
    PRIMARY KEY (source_note_id, relation, target_type, target_id)
);
CREATE TABLE anchors (
    id INTEGER PRIMARY KEY AUTOINCREMENT, graph_id TEXT NOT NULL REFERENCES graphs(id) ON DELETE CASCADE,
    target_type TEXT NOT NULL, target_id TEXT NOT NULL, repo TEXT NOT NULL, kind TEXT NOT NULL,
    ref TEXT NOT NULL, pinned_sha TEXT NOT NULL DEFAULT '', path TEXT NOT NULL DEFAULT '', start_line INTEGER,
    end_line INTEGER, symbol TEXT NOT NULL DEFAULT '', claim TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '', excerpt TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
    verified_at TEXT NOT NULL
);
CREATE TABLE proposals (
    id TEXT PRIMARY KEY, graph_id TEXT NOT NULL REFERENCES graphs(id) ON DELETE CASCADE, request_id TEXT UNIQUE,
    source TEXT NOT NULL DEFAULT 'mcp', base_revision INTEGER NOT NULL, operations_json TEXT NOT NULL,
    summary TEXT NOT NULL, rationale TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
    decision_note TEXT NOT NULL DEFAULT '', result_json TEXT, created_at TEXT NOT NULL, decided_at TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
    graph_id UNINDEXED, item_type UNINDEXED, item_id UNINDEXED, title, summary, body
);
CREATE INDEX tickets_graph ON tickets(graph_id);
CREATE INDEX notes_graph ON notes(graph_id);
CREATE INDEX anchors_target ON anchors(graph_id, target_type, target_id);
CREATE INDEX proposals_status ON proposals(graph_id, status);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def write_tx(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def init_db(db_path: Path) -> Path:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(db_path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        with write_tx(conn):
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise DomainError(f"Unsupported database schema version {version}")
            if version == 0:
                for statement in _SCHEMA.strip().rstrip(";").split(";"):
                    conn.execute(statement)
                now = utc_now()
                conn.execute("INSERT INTO graphs VALUES ('default', 'Default', 0, ?, ?)", (now, now))
                conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    return db_path


def delete_fts_item(conn, item_type, item_id):
    conn.execute("DELETE FROM search_fts WHERE item_type=? AND item_id=?", (item_type, str(item_id)))


def sync_fts_item(conn, graph_id, item_type, item_id, title, summary, body):
    delete_fts_item(conn, item_type, item_id)
    conn.execute("INSERT INTO search_fts VALUES (?, ?, ?, ?, ?, ?)",
                 (graph_id, item_type, str(item_id), title, summary, body))


def rebuild_fts(conn):
    conn.execute("DELETE FROM search_fts")
    conn.execute("""INSERT INTO search_fts
        SELECT graph_id, 'ticket', CAST(id AS TEXT), title, description, tech_notes FROM tickets
        UNION ALL SELECT graph_id, 'note', id, title, summary, body FROM notes""")
