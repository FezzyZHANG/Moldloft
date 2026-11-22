"""Preview or apply a legacy tickets workspace migration from a v2 source checkout."""

import argparse
from contextlib import ExitStack, closing, contextmanager
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import sqlite3
import sys
from tempfile import TemporaryDirectory

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from moldloft.config import resolve_repo_configs
from moldloft.db import SCHEMA_VERSION, _SCHEMA, connect, rebuild_fts, utc_now, write_tx
from moldloft.errors import DomainError
from moldloft.git_truth import GitTruth, anchor_spec
from moldloft.reducer import (EDGE_KEY, LINK_KEY, NOTE_ID_RE, NOTE_KINDS, TICKET_DEFAULTS,
                              TICKET_PRIORITIES, TICKET_STATUSES, _Graph, _validate_text, body_hash)
from moldloft.store import _insert


STATES = {kind: {state: state for state in states} for kind, states in NOTE_KINDS.items() if kind != "note"}
STATES["question"]["archived"] = "resolved"
STATES.update(idea={"open": "active", "selected": "active", "parked": "archived"},
              source={"recorded": "active", "superseded": "archived"}, metadata={"recorded": "active"})
RELATIONS = {r: r for r in ("assumes", "supports", "challenges", "relates")}
RELATIONS.update(derived_from="relates", option="relates")
COUNTS = ("graphs", "tickets", "edges", "notes", "note_links", "anchors", "verified_anchors", "unverified_anchors")
ERRORS = (ValueError, OSError, sqlite3.Error, DomainError, OverflowError)


def _report():
    return dict(dict.fromkeys(COUNTS, 0), ticket_id_map={}, note_id_map={}, errors=[], warnings=[], applied=False)


def _db_files(path):
    return [path, *(Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal"))]


def _stamp(path):
    try:
        stat = path.stat()
        return stat.st_ino, stat.st_size, stat.st_mtime_ns
    except FileNotFoundError:
        return None


@contextmanager
def _source(path):
    # SQLite can create or write shared-memory sidecars even with mode=ro.
    # For a WAL database, let it build the index beside a private copy of the WAL.
    db, wal, _, journal = _db_files(path)
    before = [_stamp(p) for p in (db, wal, journal)]
    if before[0] is None:
        raise ValueError(f"Missing legacy database {db}")
    with ExitStack() as stack:
        uri = db.as_uri() + "?mode=ro&immutable=1"
        if before[1] or before[2]:
            copy = Path(stack.enter_context(TemporaryDirectory())) / "tickets.db"
            shutil.copyfile(db, copy)
            for sidecar, stamp in ((wal, before[1]), (journal, before[2])):
                if stamp:
                    shutil.copyfile(sidecar, str(copy) + sidecar.name[len(db.name):])
            uri = copy.as_uri() + "?mode=ro"
        conn = stack.enter_context(closing(sqlite3.connect(uri, uri=True)))
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        yield conn
        if before != [_stamp(p) for p in (db, wal, journal)]:
            raise ValueError(f"{db} changed during migration. Stop legacy writers and retry")


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _frontmatter(text):
    header, delimiter, body = text[4:].partition("\n---\n")
    if not text.startswith("---\n") or not delimiter:
        raise ValueError("Expected frontmatter between --- lines")
    meta = {}
    plain = {"id", "kind", "state", "title"}
    for line in header.split("\n"):
        key, sep, value = line.partition(":")
        if not sep or key in meta or key not in plain | {"relations", "abstract_zh", "abstract_en", "adopted_digest"}:
            raise ValueError(f"Unexpected or duplicate frontmatter field {key!r}")
        value = value.strip()
        try:
            meta[key] = json.loads(value)
        except RecursionError as exc:
            raise ValueError(f"Excessive JSON nesting in {key}") from exc
        except json.JSONDecodeError:
            if key not in plain or not value or value[0] in "[{\"'":
                raise ValueError(f"Invalid JSON in {key}") from None
            meta[key] = value
    if any(not isinstance(meta.get(k), str) for k in plain):
        raise ValueError("Expected text for id, title, kind and state")
    return meta, body


def _note(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("Expected a regular legacy note file")
    with path.open("rb") as f:
        raw = f.read(128001)
    if len(raw) > 128000:
        raise ValueError("Note exceeds the legacy 128000-byte limit")
    meta, body = _frontmatter(raw.decode("utf-8-sig").replace("\r\n", "\n"))
    key, title, kind, state = (meta[k] for k in ("id", "title", "kind", "state"))
    if key != path.stem or not NOTE_ID_RE.fullmatch(key):
        raise ValueError("Note ID must be valid and match the filename")
    title = title.strip()  # The legacy digest used the validated title.
    _validate_text(dict(title=title, body=body))
    if state not in STATES.get(kind, {}):
        raise ValueError(f"Invalid legacy kind/state {kind}/{state}")
    abstracts = [meta.get(k, "") for k in ("abstract_zh", "abstract_en")]
    if not all(isinstance(a, str) for a in abstracts):
        raise ValueError("Expected text for abstracts")
    summary = "\n\n".join(a for a in abstracts if a)
    digest = meta.get("adopted_digest")
    if digest is not None and (not isinstance(digest, str) or not re.fullmatch("[a-f0-9]{64}", digest)):
        raise ValueError("Invalid adopted_digest")
    original = sha256(json.dumps([title, body], ensure_ascii=False).encode("utf-8")).hexdigest()
    if len(summary) > 1000:
        body = summary + "\n\n" + body
        summary = summary[:999] + "…"
    adopted = None
    if kind == "assumption" and digest == original:
        adopted = body_hash(title, body)
    elif kind == "assumption" and state == "adopted":
        adopted = "legacy-stale"
    note = dict(id=key, title=title, kind=kind if kind in NOTE_KINDS else "note", state=STATES[kind][state],
                summary=summary, body=body, in_graph=kind != "metadata", adopted_body_hash=adopted)
    return note, _relations(key, kind, meta.get("relations", []))


def _relations(key, kind, relations):
    links = []
    owner = dict(assumes="assumption", supports="evidence", challenges="evidence", derived_from="evidence", option="idea")
    if not isinstance(relations, list):
        raise ValueError("Expected a relations array")
    for link in relations:
        if not isinstance(link, dict) or set(link) != {"type", "target"}:
            raise ValueError("Expected type and target on each relation")
        relation, target = link["type"], link["target"]
        if not isinstance(relation, str) or relation not in RELATIONS or not isinstance(target, str):
            raise ValueError(f"Invalid relation {link!r}")
        if owner.get(relation, kind) != kind or (relation == "derived_from" and not target.startswith("note:")):
            raise ValueError(f"Invalid {relation} relation from {kind} to {target}")
        target_type, _, target_id = target.partition(":")
        if target_type == "ticket" and re.fullmatch("[1-9][0-9]*", target_id):
            target_id = int(target_id)
        elif target_type != "note" or not NOTE_ID_RE.fullmatch(target_id) or target_id == key:
            raise ValueError(f"Invalid relation target {target!r}")
        links.append((RELATIONS[relation], target_type, target_id))
    return links


def _read_dock(conn, graph, directory, required, inputs, report):
    tables = _tables(conn)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version not in range(1, 5) or not {"tickets", "ticket_edges"} <= tables or "graphs" in tables:
        raise ValueError(f"Incomplete or unsupported legacy schema (user_version={version})")
    if "note_write_attempts" in tables and conn.execute("SELECT 1 FROM note_write_attempts LIMIT 1").fetchone():
        raise ValueError("Unresolved note_write_attempts. Finish recovery in legacy Moldloft first")
    if "proposals" in tables:
        count = conn.execute("SELECT count(*) FROM proposals WHERE status='pending'").fetchone()[0]
        if count:
            report["warnings"].append(f"{graph['id']}: {count} pending legacy proposal(s). Finish or discard before cutover")
    tickets = [dict(r) for r in conn.execute("SELECT * FROM tickets ORDER BY id")]
    edges = [tuple(r) for r in conn.execute("SELECT blocker_id, blocked_id FROM ticket_edges")]
    anchors = []
    if "code_links" in tables:
        anchors = [dict(r) for r in conn.execute("""SELECT ticket_id, repo, kind, ref, path, start_line, end_line
                                                    FROM code_links ORDER BY id""")]
    floor = 0
    if "graph_meta" in tables:
        meta = conn.execute("SELECT revision, next_ticket_id FROM graph_meta WHERE id=1").fetchone()
        if meta:
            if type(meta["revision"]) is not int or meta["revision"] < 0:
                raise ValueError("Invalid graph_meta revision")
            if type(meta["next_ticket_id"]) is not int or meta["next_ticket_id"] < 1:
                raise ValueError("Invalid graph_meta next_ticket_id")
            graph["revision"], floor = meta["revision"], meta["next_ticket_id"] - 1
    if "sqlite_sequence" in tables:
        seq = conn.execute("SELECT seq FROM sqlite_sequence WHERE name='tickets'").fetchone()
        if seq:
            if type(seq[0]) is not int or seq[0] < 0:
                raise ValueError("Invalid legacy ticket sequence")
            floor = max(floor, seq[0])
    notes = {}
    if required or directory.exists() or directory.is_symlink():
        # iterdir surfaces missing/unreadable notebooks, including an unavailable secondary.
        for path in sorted(directory.iterdir()):
            if not path.match("n-*.md"):
                continue
            inputs.add(path)
            try:
                notes[path.stem] = _note(path)
            except ERRORS as exc:
                message = f"{graph['id']}: {path}: {exc}"
                report["warnings"].append(message)
                report["errors"].append(message)
    return dict(graph=graph, floor=floor, tickets=tickets, edges=edges, notes=notes, anchors=anchors, directory=directory)


def _read_workspace(source, notes, explicit_notes, inputs, report, now):
    docks = []
    with _source(source) as primary:
        catalog = {"default": dict(id="default", name="Default")}
        if "drydocks" in _tables(primary):
            catalog.update((r["id"], dict(r)) for r in primary.execute("SELECT * FROM drydocks ORDER BY id"))
        for key, row in catalog.items():
            if not isinstance(key, str) or not re.fullmatch("[A-Za-z0-9][A-Za-z0-9_-]*", key):
                raise ValueError(f"Invalid Drydock ID {key!r}")
            secondary = key != "default"
            path = (source.parent / "drydocks" / key / "tickets.db").resolve() if secondary else source
            directory = notes.parent / "drydocks" / key / "notes" if secondary else notes
            inputs.update(_db_files(path))
            created = row.get("created_at") or now
            if not isinstance(row.get("name"), str) or not row["name"].strip():
                raise ValueError(f"Missing Drydock name for {key}")
            graph = dict(id=key, name=row["name"], revision=0, created_at=created, updated_at=created)
            try:
                with ExitStack() as stack:
                    conn = stack.enter_context(_source(path)) if secondary else primary
                    docks.append(_read_dock(conn, graph, directory, secondary or explicit_notes, inputs, report))
            except ERRORS as exc:
                report["errors"].append(f"Drydock {key} ({path}): {exc}")
    return docks


def _renamed(key, reserved):
    number = 2
    while True:
        suffix = f"-{number}"
        candidate = key[:64 - len(suffix)] + suffix
        if candidate not in reserved:
            return candidate
        number += 1


def _remap(mapping, key, graph):
    if key not in mapping:
        raise ValueError(f"{graph}: missing ticket {key}")
    return mapping[key]


def _ticket(row, ids, graph, now):
    ticket = {k: row.get(k, v) for k, v in TICKET_DEFAULTS.items()}
    _validate_text(ticket)
    if ticket["status"] not in TICKET_STATUSES or ticket["priority"] not in TICKET_PRIORITIES:
        raise ValueError(f"{graph}: invalid status or priority on ticket {row['id']}")
    if ticket["parent_id"] is not None:
        ticket["parent_id"] = _remap(ids, ticket["parent_id"], graph)
    return dict(ticket, id=ids[row["id"]], graph_id=graph, version=row.get("version", 1),
                created_at=row.get("created_at", now), updated_at=row.get("updated_at", now))


def _anchor(row, anchor_id, graph, target, truth, now, warnings):
    spec = dict(row, path=row["path"] or "")
    if spec["kind"] == "file" and (spec["start_line"], spec["end_line"]) == (1, 0):
        spec["end_line"] = 1  # Legacy represented a pinned empty file as lines 1–0.
    fields = anchor_spec(spec)
    try:
        fields = truth.pin(fields)
        verified = now
    except DomainError as exc:
        fields.update(pinned_sha="", content_hash="", excerpt="")
        verified = ""
        warnings.append(f"{graph}/ticket:{target}: unverified {row['repo']}@{row['ref']}: {exc}")
    return dict(fields, id=anchor_id, graph_id=graph, target_type="ticket", target_id=target,
                created_at=now, verified_at=verified)


def _convert(docks, truth, report, now):
    reserved = {key for dock in docks for key in dock["notes"]}
    used = set()
    ticket_seq = anchor_seq = 0
    tables = {name: [] for name in ("graphs", "tickets", "notes", "ticket_edges", "note_links", "anchors")}
    for dock in docks:
        graph = dock["graph"]["id"]
        tables["graphs"].append(dock["graph"])
        ids = {}
        for row in dock["tickets"]:
            old = row["id"]
            if type(old) is not int or old < 1:
                raise ValueError(f"{graph}: invalid ticket ID {old!r}")
            ids[old] = old if graph == "default" else ticket_seq + 1
            ticket_seq = max(ticket_seq, ids[old])
        if graph == "default":
            ticket_seq = max(ticket_seq, dock["floor"])
        report["ticket_id_map"][graph] = {str(k): v for k, v in ids.items()}
        note_ids = report["note_id_map"][graph] = {}
        for key in dock["notes"]:
            new = _renamed(key, reserved) if key in used else key
            note_ids[key] = new
            if new != key:
                report["warnings"].append(f"{graph}: renamed colliding note {key} to {new}")
            used.add(new)
            reserved.add(new)
        notes, links, anchors = [], set(), []
        tickets = [_ticket(row, ids, graph, now) for row in dock["tickets"]]
        edges = [dict(zip(EDGE_KEY, (_remap(ids, k, graph) for k in edge))) for edge in dock["edges"]]
        for key, (note, relations) in dock["notes"].items():
            notes.append(dict(note, id=note_ids[key], graph_id=graph, created_at=now, updated_at=now))
            if len(note["body"]) > 20000:
                report["warnings"].append(f"{graph}/{key}: full abstracts preserved. Body exceeds the v2 "
                                          "editor and archive import limit of 20000 characters")
            for relation, kind, target in relations:
                mapping = ids if kind == "ticket" else note_ids
                if target in mapping:
                    links.add((note_ids[key], relation, kind, mapping[target]))
                else:
                    report["warnings"].append(f"{graph}/{key}: dangling {relation} target {kind}:{target}")
        for row in dock["anchors"]:
            target = _remap(ids, row["ticket_id"], graph)
            anchor_seq += 1
            anchors.append(_anchor(row, anchor_seq, graph, target, truth, now, report["warnings"]))
        snapshot = _Graph(dict(tickets=tickets, edges=edges, notes=notes, anchors=anchors,
                               note_links=[dict(zip(LINK_KEY, link)) for link in links])).finish()
        for name, rows in snapshot.items():
            tables["ticket_edges" if name == "edges" else name].extend(rows)
    for name, rows in tables.items():
        report["edges" if name == "ticket_edges" else name] = len(rows)
    report["verified_anchors"] = sum(bool(a["verified_at"]) for a in tables["anchors"])
    report["unverified_anchors"] = anchor_seq - report["verified_anchors"]
    return tables, dict(tickets=ticket_seq, anchors=anchor_seq)


def _protect_inputs(target, inputs, notebooks):
    if target in notebooks or (target.parent in notebooks and target.match("n-*.md")):
        raise ValueError(f"Target overlaps legacy source notebook {target}")
    protected = {path.resolve() for path in inputs}
    stats = (p.stat() for p in protected if p.exists())
    identities = {(s.st_dev, s.st_ino) for s in stats}
    for path in _db_files(target):
        if path.resolve() in protected:
            raise ValueError(f"Target overlaps legacy source {path}")
        if path.exists():
            stat = path.stat()
            if (stat.st_dev, stat.st_ino) in identities:
                raise ValueError(f"Target aliases legacy source file {path}")


def _write(conn, tables, sequences, replace):
    # Foreign keys in an arbitrary previous target schema can reference tables being dropped.
    if replace:
        conn.execute("PRAGMA foreign_keys=OFF")
    with write_tx(conn):
        objects = conn.execute("""SELECT type, name FROM sqlite_master
            WHERE type IN ('table','view','trigger') AND name NOT GLOB 'sqlite_*'
            ORDER BY CASE type WHEN 'trigger' THEN 0 WHEN 'view' THEN 1
                ELSE CASE WHEN sql LIKE 'CREATE VIRTUAL TABLE%' THEN 2 ELSE 3 END END""").fetchall()
        if objects and not replace:
            raise ValueError("Target database is not empty. Pass --replace to overwrite it")
        for kind, name in objects:
            quoted = '"' + name.replace('"', '""') + '"'
            conn.execute(f"DROP {kind} IF EXISTS {quoted}")
        for statement in _SCHEMA.strip().rstrip(";").split(";"):
            conn.execute(statement)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.execute("PRAGMA defer_foreign_keys=ON")
        for name, rows in tables.items():
            for row in rows:
                _insert(conn, name, row)
        for name, sequence in sequences.items():
            conn.execute("DELETE FROM sqlite_sequence WHERE name=?", (name,))
            conn.execute("INSERT INTO sqlite_sequence(name,seq) VALUES (?,?)", (name, sequence))
        rebuild_fts(conn)
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Migrated data contains a broken foreign key")


def migrate_legacy(source_db, target_db=None, *, source_notes=None, repos=None, apply=False, replace=False) -> dict:
    """Return counts, ID maps and diagnostics. Any errors prevent publication."""
    report = _report()
    try:
        if apply and target_db is None:
            raise ValueError("--apply requires --target-db")
        source = Path(source_db).expanduser().resolve()
        notes = Path(source_notes).expanduser().resolve() if source_notes is not None else source.parent / "notes"
        inputs = set()
        now = utc_now()
        docks = _read_workspace(source, notes, source_notes is not None, inputs, report, now)
        if report["errors"]:
            return report
        tables, sequences = _convert(docks, GitTruth(repos), report, now)
        target = Path(target_db).expanduser().resolve() if target_db is not None else None
        if target is not None:
            _protect_inputs(target, inputs, {d["directory"].resolve() for d in docks})
            if target.is_dir():
                raise ValueError(f"Target database path is a directory: {target}")
            if target.exists() and target.stat().st_size and not replace:
                raise ValueError("Target database is not empty. Pass --replace to overwrite it")
        if apply:
            target.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(target if apply else ":memory:")) as conn:
            _write(conn, tables, sequences, replace)
            if apply:
                report["applied"] = True
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                except sqlite3.Error as exc:
                    report["warnings"].append(f"Migration committed. Close other target connections to enable WAL: {exc}")
    except ERRORS as exc:
        report["errors"].append(str(exc))
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", required=True, type=Path)
    parser.add_argument("--source-notes", type=Path)
    parser.add_argument("--target-db", type=Path)
    parser.add_argument("--repos", metavar="FILE")
    parser.add_argument("--repo", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--apply", action="store_true", help="write the target (default: preview in memory)")
    parser.add_argument("--replace", action="store_true", help="replace an existing target database")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        roots = {}
        for entry in args.repo:
            name, sep, path = entry.partition("=")
            if not sep or not path:
                raise ValueError("Expected --repo NAME=PATH")
            roots[name] = path
        report = migrate_legacy(args.source_db, args.target_db, source_notes=args.source_notes,
                                repos=resolve_repo_configs(roots, args.repos), apply=args.apply, replace=args.replace)
    except ERRORS as exc:
        report = _report()
        report["errors"].append(str(exc))
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=True))
    else:
        print("Migration failed" if report["errors"] else "Applied" if report["applied"] else "Dry run")
        print(", ".join(f"{report[k]} {k}" for k in COUNTS))
        for level in ("warnings", "errors"):
            for message in report[level]:
                print(f"{level}: {message}")
    return int(bool(report["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
