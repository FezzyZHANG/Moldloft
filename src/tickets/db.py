"""Database lifecycle. Imports never create or migrate a database."""
from __future__ import annotations

import datetime as dt
import sqlite3
from contextlib import closing
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, select, func
from sqlalchemy.orm import Session

from .models import Base, GraphMeta, Ticket

from .config import default_db_path

DB_PATH = default_db_path()
SCHEMA_VERSION = 3


def initialize(path: Path):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    # sqlite.backup also works while an older server has an open connection.
    if path.exists() and path.stat().st_size:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
            version = source.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError("Database schema is newer than this application")
            if version < SCHEMA_VERSION:
                stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                backup = path.parent / "backups" / f"{path.stem}-before-v{SCHEMA_VERSION}-{stamp}.sqlite3"
                backup.parent.mkdir(exist_ok=True)
                with closing(sqlite3.connect(backup)) as destination:
                    source.backup(destination)
    engine = create_engine(f"sqlite:///{path.as_posix()}", connect_args={"check_same_thread": False, "timeout": 5})

    @event.listens_for(engine, "connect")
    def configure(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")

    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        if inspect(connection).has_table("tickets"):
            columns = {c["name"] for c in inspect(connection).get_columns("tickets")}
            additions = {
                "title_zh": "TEXT NOT NULL DEFAULT ''", "description_zh": "TEXT NOT NULL DEFAULT ''",
                "group_name": "TEXT NOT NULL DEFAULT ''", "tech_notes": "TEXT NOT NULL DEFAULT ''",
                "parent_id": "INTEGER REFERENCES tickets(id) ON DELETE SET NULL",
                "version": "INTEGER NOT NULL DEFAULT 1", "source_version": "INTEGER NOT NULL DEFAULT 1",
                "translated_version": "INTEGER", "translation_due": "FLOAT",
                "translation_error": "BOOLEAN NOT NULL DEFAULT 0",
            }
            for name, ddl in additions.items():
                if name not in columns:
                    connection.exec_driver_sql(f"ALTER TABLE tickets ADD COLUMN {name} {ddl}")
        Base.metadata.create_all(connection)
        with Session(bind=connection) as session:
            if session.get(GraphMeta, 1) is None:
                next_id = (session.scalar(select(func.max(Ticket.id))) or 0) + 1
                session.add(GraphMeta(id=1, revision=0, next_ticket_id=next_id))
                session.flush()
        connection.exec_driver_sql(f"PRAGMA user_version={SCHEMA_VERSION}")
    return engine
