"""Writer exclusion and exception compensation, shared by both source stores.

The auxiliary SQLite file carries only a lock, never authoritative data. Recovery
evidence lives in the existing ticket DB. Interrupted attempts block writes until recovery;
proposal apply supplies the intended bytes and assigned-ID context for planned recovery.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager, nullcontext
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import uuid

from sqlalchemy import delete, select

from .models import NoteWriteAttempt


def fail(message):
    from .service import DomainError
    return DomainError(message, 503)


def read_bytes(path):
    if path.is_symlink():
        raise fail('Linked note paths are not supported')
    try:
        with path.open('rb') as source:
            raw = source.read(128_001)
        if len(raw) > 128_000:
            raise fail('Note exceeds the file size limit')
        return raw
    except FileNotFoundError:
        return None


def replace_bytes(path, raw):
    """Same-directory replacement; also used to restore exact prior bytes."""
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.note-', suffix='.tmp', delete=False) as output:
            temporary = Path(output.name)
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


@contextmanager
def writer_gate(engine):
    # A separate database lock survives ROLLBACK/failed COMMIT in the ticket DB.
    # sqlite coordinates threads and processes, including different Engine objects.
    path = Path(engine.url.database).resolve()
    gate = sqlite3.connect(str(path) + '.writer-lock', timeout=5)
    try:
        gate.execute('BEGIN IMMEDIATE')
        yield
    except sqlite3.OperationalError as error:
        raise fail('Writer is busy or unavailable; keep your draft and retry') from error
    finally:
        gate.close()


def register_replace(connection, path, before, after):
    """Require a declared file and a live managed transaction before replacing it."""
    state = connection.info.get('note_write_scope')
    path = Path(path).absolute()
    if state is None or not connection.in_transaction() or path not in state['files']:
        raise fail('Note write requires a managed transaction declaring this file')
    entry = state['files'][path]
    if before != entry['expected']:
        raise fail('Note changed outside the writer lock; keep your draft')
    # Record intent before replace: compensation accepts either old or intended bytes
    # if replacement itself raises. Never overwrite unrelated external content.
    entry['previous_expected'] = before
    entry['expected'] = after
    entry['touched'] = True


def compensate(files):
    for path, entry in reversed(list(files.items())):
        if not entry['touched']:
            continue
        current = read_bytes(path)
        if current == entry['before']:
            continue
        if current not in (entry['expected'], entry.get('previous_expected')):
            raise fail('External note edit conflicts with recovery')
        if entry['before'] is None:
            path.unlink()
        else:
            replace_bytes(path, entry['before'])


def _commit(connection):
    connection.commit()


@contextmanager
def transaction(engine, note_paths=(), *, planned=None, context=None, guard_held=False):
    """Own BEGIN/COMMIT and compensate declared files while retaining writer exclusion.

Callers must not commit or roll back this connection themselves. Declare every note
path before staging SQL; before-images are persisted before the SQL transaction starts.
The attempt is removed in the same commit as staged SQL, resolving uncertain COMMIT.
"""
    with nullcontext() if guard_held else writer_gate(engine):
        with engine.connect() as check:
            if check.execute(select(NoteWriteAttempt.id).limit(1)).first():
                raise fail('Unresolved note write recovery; ordinary writes are blocked')
        files = {}
        for path in note_paths:
            path = Path(path).absolute()
            raw = read_bytes(path)
            files[path] = {'before': raw, 'expected': raw, 'touched': False}
        if planned is not None and set(files) != set(planned):
            raise fail('Recovery plan must cover exactly the declared files')
        attempt_id = str(uuid.uuid4()) if files else None
        if attempt_id:
            evidence = [{'path': str(path), 'before': None if item['before'] is None else
                         base64.b64encode(item['before']).decode('ascii')} for path, item in files.items()]
            if planned is not None:
                for entry in evidence:
                    entry['after'] = base64.b64encode(planned[Path(entry['path'])]).decode('ascii')
                evidence = {'files': evidence, 'context': context or {}}
            with engine.begin() as journal:
                journal.execute(NoteWriteAttempt.__table__.insert().values(
                    id=attempt_id, files_json=json.dumps(evidence)))
        with engine.connect() as connection:
            connection.info['note_write_scope'] = {'files': files}
            try:
                connection.exec_driver_sql('BEGIN IMMEDIATE')
                yield connection
                if not connection.in_transaction():
                    raise fail('Managed write transaction was ended by its caller')
                if attempt_id:
                    connection.execute(delete(NoteWriteAttempt).where(NoteWriteAttempt.id == attempt_id))
                if planned is not None:
                    for path, raw in planned.items():
                        if read_bytes(path) != raw:
                            raise fail('Written note does not match the durable recovery plan')
                fault_point('before_commit')
                _commit(connection)
                fault_point('after_commit')
            except BaseException as error:
                connection.rollback()
                if attempt_id:
                    # COMMIT may have succeeded before the driver reported an error.
                    # If its marker disappeared, committed SQL and note bytes must stay.
                    with engine.connect() as check:
                        pending = check.execute(select(NoteWriteAttempt.id).where(
                            NoteWriteAttempt.id == attempt_id)).first()
                    if pending:
                        try:
                            compensate(files)
                            with engine.begin() as journal:
                                journal.execute(delete(NoteWriteAttempt).where(NoteWriteAttempt.id == attempt_id))
                        except BaseException as recovery_error:
                            raise fail('Note recovery failed; prior bytes retained in note_write_attempts; '
                                       'ordinary writes are blocked') from recovery_error
                raise error
            finally:
                connection.info.pop('note_write_scope', None)


def fault_point(name):
    """Injection seam for disposable-worker crash tests; production has no side effect."""


def recover_pending(engine, notebook_root, *, check_only=False):
    """Recover planned interrupted writes under the same writer exclusion.

Legacy before-image-only attempts can be cleared only when every file already equals
its prior bytes. Unknown/external contents remain intact with a visible blocked state.
"""
    root = Path(notebook_root).resolve()
    from .notes import NOTE_ID
    with writer_gate(engine):
        with engine.connect() as connection:
            attempts = connection.execute(select(NoteWriteAttempt.id, NoteWriteAttempt.files_json)).all()
        reports = []
        for attempt_id, raw in attempts:
            try:
                record = json.loads(raw)
                entries = record if isinstance(record, list) else record['files']
                planned = not isinstance(record, list)
                files = {}
                for entry in entries:
                    path = Path(entry['path'])
                    if path.parent.resolve() != root or path.suffix != '.md' or not NOTE_ID.fullmatch(path.stem):
                        raise fail('Recovery record targets an unexpected notebook path')
                    prior = None if entry['before'] is None else base64.b64decode(entry['before'], validate=True)
                    after = base64.b64decode(entry['after'], validate=True) if planned else prior
                    current = read_bytes(path)
                    if current != prior and current != after:
                        raise fail('Recovery conflicts with external or unplanned note content')
                    files[path] = {'before': prior, 'expected': after, 'touched': True}
                reports.append({'id': attempt_id, 'files': [p.name for p in files], 'recoverable': True})
                if not check_only:
                    for index, (path, item) in enumerate(reversed(list(files.items()))):
                        compensate({path: item})
                        fault_point(f'recovery_file_{index}')
                    with engine.begin() as connection:
                        connection.execute(delete(NoteWriteAttempt).where(NoteWriteAttempt.id == attempt_id))
            except (ValueError, KeyError, TypeError, OSError) as error:
                raise fail('Recovery evidence is invalid or unavailable; ordinary writes remain blocked') from error
        return {'attempts': reports, 'recovered': not check_only, 'blocked': False}
