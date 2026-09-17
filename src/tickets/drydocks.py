"""Named graph stores with immutable identity and no process-global selection."""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
import hashlib
from pathlib import Path
import re
import sqlite3
import threading
import unicodedata
import uuid

from sqlalchemy import select, func
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from .db import initialize
from .models import Drydock
from .notes import Notebook
from .proposals import Proposals
from .service import DomainError, Store
from .writes import recover_pending, writer_gate

DEFAULT_DRYDOCK = "default"
MAX_DRYDOCKS = 100
DRYDOCK_ID = re.compile(r"default|d-[0-9a-f]{32}")


def public(row):
    return {"id": row.id, "name": row.name, "version": row.version,
            "created_at": row.created_at.isoformat(), "is_default": row.id == DEFAULT_DRYDOCK}


def valid_name(value):
    if not isinstance(value, str):
        raise DomainError("Drydock name must be text")
    value = unicodedata.normalize("NFC", value.strip())
    if not value or len(value) > 80 or any(unicodedata.category(c) in ('Cc', 'Zl', 'Zp') for c in value):
        raise DomainError("Drydock name must contain 1-80 characters on one line")
    return value


@dataclass
class DockContext:
    id: str
    store: Store
    notes: Notebook
    proposals: Proposals
    recovery_error: str | None = None


class Drydocks:
    def __init__(self, database, notebook, git=None):
        self.database, self.notebook, self.git = Path(database).resolve(), Path(notebook).resolve(), git
        self.engine = initialize(self.database)
        self._contexts = {}
        self._lock = threading.RLock()
        with writer_gate(self.engine), self.engine.begin() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            with Session(connection) as session:
                if session.get(Drydock, DEFAULT_DRYDOCK) is None:
                    session.add(Drydock(id=DEFAULT_DRYDOCK, name="Default", name_key="default"))
                    session.flush()

    def describe(self, drydock_id):
        if not isinstance(drydock_id, str) or not DRYDOCK_ID.fullmatch(drydock_id):
            raise DomainError("Drydock not found", 404)
        with Session(self.engine) as session:
            row = session.get(Drydock, drydock_id)
            if row is None:
                raise DomainError("Drydock not found", 404)
            return public(row)

    def list(self):
        with Session(self.engine) as session:
            rows = session.scalars(select(Drydock).order_by(Drydock.created_at, Drydock.id)).all()
            return {"items": [public(row) for row in rows], "default_id": DEFAULT_DRYDOCK,
                    "limit": MAX_DRYDOCKS}

    def paths(self, drydock_id):
        self.describe(drydock_id)
        if drydock_id == DEFAULT_DRYDOCK:
            return self.database, self.notebook
        return (self.database.parent / "drydocks" / drydock_id / "tickets.db",
                self.notebook.parent / "drydocks" / drydock_id / "notes")

    def available_paths(self, drydock_id):
        database, notebook = self.paths(drydock_id)
        if drydock_id != DEFAULT_DRYDOCK and (not database.is_file() or not notebook.is_dir()):
            raise DomainError("Drydock data is unavailable; restore its DB and notebook", 503)
        return database, notebook

    def create(self, name, request_id):
        name = valid_name(name)
        if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", request_id):
            raise DomainError("A stable request ID of 8-80 characters is required")
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
        with writer_gate(self.engine), self.engine.begin() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            with Session(connection) as session:
                existing = session.scalar(select(Drydock).where(Drydock.request_id == request_id))
                if existing:
                    if existing.request_hash != digest:
                        raise DomainError("Request ID was already used with another name", 409)
                    return public(existing)
                if session.scalar(select(Drydock.id).where(Drydock.name_key == name.casefold())):
                    raise DomainError("A Drydock already has that name", 409)
                if session.scalar(select(func.count()).select_from(Drydock)) >= MAX_DRYDOCKS:
                    raise DomainError("This test version supports at most 100 Drydocks", 409)
                # Unknown IDs never cause initialization. A new ID becomes discoverable
                # only after its empty DB/notebook exist and the catalog row commits.
                drydock_id = "d-" + uuid.uuid4().hex
                database = self.database.parent / "drydocks" / drydock_id / "tickets.db"
                notebook = self.notebook.parent / "drydocks" / drydock_id / "notes"
                notebook.mkdir(parents=True, exist_ok=False)
                engine = initialize(database)
                engine.dispose()
                row = Drydock(id=drydock_id, name=name, name_key=name.casefold(),
                              request_id=request_id, request_hash=digest)
                session.add(row)
                session.flush()
                return public(row)

    def rename(self, drydock_id, name, version):
        self.describe(drydock_id)
        name = valid_name(name)
        with writer_gate(self.engine), self.engine.begin() as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            with Session(connection) as session:
                row = session.get(Drydock, drydock_id)
                # Lost acknowledgement may retry the already committed same-name edit.
                if type(version) is not int or (row.version != version and not (
                        row.version == version + 1 and row.name == name)):
                    raise DomainError("Drydock changed; refresh its name before retrying", 409, current=public(row))
                if row.name == name:
                    return public(row)
                if session.scalar(select(Drydock.id).where(Drydock.name_key == name.casefold(), Drydock.id != drydock_id)):
                    raise DomainError("A Drydock already has that name", 409)
                row.name, row.name_key = name, name.casefold()
                row.version += 1
                session.flush()
                return public(row)

    def get(self, drydock_id=DEFAULT_DRYDOCK):
        database, notebook = self.available_paths(drydock_id)
        # A missing existing store is unknown/unavailable, never a newly empty graph.
        with self._lock:
            if drydock_id not in self._contexts:
                try:
                    if drydock_id != DEFAULT_DRYDOCK:
                        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as source:
                            if source.execute('SELECT id FROM graph_meta WHERE id=1').fetchone() is None:
                                raise DomainError('Drydock database is incomplete', 503)
                    engine = self.engine if drydock_id == DEFAULT_DRYDOCK else initialize(database)
                except (OSError, sqlite3.DatabaseError, SQLAlchemyError, RuntimeError) as error:
                    raise DomainError('Drydock database is unavailable; preserve it for recovery', 503) from error
                book = Notebook(notebook, engine)
                store = Store(engine, self.git, book, drydock_id=drydock_id)
                context = DockContext(drydock_id, store, book, Proposals(store))
                try:
                    recover_pending(engine, notebook)
                except DomainError as error:
                    context.recovery_error = error.message
                self._contexts[drydock_id] = context
            return self._contexts[drydock_id]

    def opened(self):
        with self._lock:
            return list(self._contexts.values())

    def close(self):
        with self._lock:
            for context in self._contexts.values():
                if context.store.engine is not self.engine:
                    context.store.engine.dispose()
            self._contexts.clear()
            self.engine.dispose()
