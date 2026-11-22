"""Markdown editors and deterministic, offline SQLite workspace exchange."""

from collections import defaultdict
from contextlib import ExitStack, closing
from hashlib import sha256
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shlex
import sqlite3
import subprocess
import tempfile

import yaml

from .db import connect, init_db, rebuild_fts, write_tx
from .errors import DomainError
from .git_truth import anchor_spec
from .reducer import (ANCHOR_FIELDS, EDGE_KEY, LINK_KEY, NOTE_ID_RE, NOTE_KINDS, NOTE_RELATIONS,
                      TICKET_PRIORITIES, TICKET_STATUSES, _Graph, _OP_KEYS, _validate_text)
from .store import Store, _insert, _rows, _snapshot

_IDENTITY = ("id", "graph_id", "version")
_DATES = ("created_at", "updated_at")
_TICKET = (*_IDENTITY, "title", "status", "priority", "group_name", "parent_id")
_NOTE = (*_IDENTITY, "title", "kind", "state", "in_graph", "summary")
_GRAPH = ("id", "name", "revision", *_DATES)
_ANCHOR = ("id", "graph_id", "target_type", "target_id", *ANCHOR_FIELDS, "pinned_sha",
           "content_hash", "excerpt", "created_at", "verified_at")
_PROPOSAL = ("id", "graph_id", "request_id", "source", "base_revision", "operations_json",
             "summary", "rationale", "status", "decision_note", "result_json", "created_at", "decided_at")
_SENTINEL = "\n<!-- moldloft:tech_notes -->\n"


def _select(row, keys):
    return {k: row[k] for k in keys}


def _yaml(data):
    text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    if "\x85" in text:
        # YAML folds a literal Unicode NEL to a space. Double quotes preserve it as \N.
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_style='"')
    return text


def _load(text):
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise DomainError(f"Invalid YAML: {exc}") from exc


def _keys(value, required, optional=()):
    if not isinstance(value, dict) or set(required) - value.keys() or value.keys() - set((*required, *optional)):
        raise DomainError(f"Expected fields {', '.join(required)}")


def _text_fields(row, keys):
    for key in keys:
        if not isinstance(row[key], str):
            raise DomainError(f"Expected text for {key}")


def _escape(body):
    # Quote our reserved comment namespace, including literal separators. Keep files LF-only.
    return body.replace("<!-- moldloft:", "<!-- moldloft:literal:").replace("\r", "<!-- moldloft:cr -->")


def _unescape(body):
    return body.replace("<!-- moldloft:cr -->", "\r").replace("<!-- moldloft:literal:", "<!-- moldloft:")


def _document(meta, body):
    return "---\n" + _yaml(meta) + "---\n" + body


def _split(text):
    text = text.replace("\r\n", "\n")
    header, delimiter, body = text[4:].partition("\n---\n")
    if not text.startswith("---\n") or not delimiter:
        raise DomainError("Expected YAML frontmatter between --- lines")
    return _load(header), body


def _ticket_document(ticket, audit=()):
    body = _escape(ticket["description"]) + _SENTINEL + _escape(ticket["tech_notes"])
    return _document(_select(ticket, (*_TICKET, *audit)), body)


def render_ticket_md(ticket: dict) -> str:
    return _ticket_document(ticket)


def parse_ticket_md(text: str) -> tuple[dict, dict]:
    meta, body = _split(text)
    _keys(meta, _TICKET, _DATES)
    description, _, tech = body.partition(_SENTINEL)
    fields = dict(_select(meta, _TICKET[3:]), description=_unescape(description), tech_notes=_unescape(tech))
    _validate_text(fields)
    if fields["status"] not in TICKET_STATUSES or fields["priority"] not in TICKET_PRIORITIES:
        raise DomainError("Invalid ticket status or priority")
    if fields["parent_id"] is not None:
        _integer(fields["parent_id"], 1)
    return meta, fields


def _links(links):
    if not isinstance(links, list):
        raise DomainError("Expected links list")
    result = []
    for link in links:
        _keys(link, LINK_KEY[1:])
        kind, key = link["target_type"], link["target_id"]
        if kind == "ticket":
            if isinstance(key, str) and re.fullmatch(r"[0-9]{1,19}", key):
                key = int(key)
            _integer(key, 1)
        elif kind != "note" or not isinstance(key, str) or not NOTE_ID_RE.fullmatch(key):
            raise DomainError("Invalid note link target")
        if link["relation"] not in NOTE_RELATIONS:
            raise DomainError("Invalid note link relation")
        result.append(dict(relation=link["relation"], target_type=kind, target_id=key))
    return result


def _note_document(note, links, audit=()):
    meta = _select(note, (*_NOTE, *audit))
    meta["links"] = [_select(link, LINK_KEY[1:]) for link in links]
    return _document(meta, _escape(note["body"]))


def render_note_md(note: dict, links: list[dict]) -> str:
    return _note_document(note, links)


def parse_note_md(text: str) -> tuple[dict, dict]:
    meta, body = _split(text)
    _keys(meta, (*_NOTE, "links"), (*_DATES, "adopted_body_hash"))
    fields = dict(_select(meta, _NOTE[3:]), links=_links(meta["links"]), body=_unescape(body))
    _validate_text(fields)
    if not isinstance(fields["kind"], str) or fields["state"] not in NOTE_KINDS.get(fields["kind"], ()):
        raise DomainError("Invalid note kind or state")
    if type(fields["in_graph"]) is not bool:
        raise DomainError("in_graph must be a boolean")
    return meta, fields


def edit_in_editor(store: Store, target: str, *, editor=None, graph_id=None) -> dict:
    loaded = store.get_item(target, graph_id)
    item = loaded["item"]
    is_ticket = "status" in item
    kind = "ticket" if is_ticket else "note"
    links = [_select(n, LINK_KEY[1:]) for n in loaded["neighbors"] if n["direction"] == "out"]
    original = (render_ticket_md(item) if is_ticket else render_note_md(item, links)).encode("utf-8")
    with tempfile.NamedTemporaryFile(prefix=f"moldloft-{kind}-{item['id']}-", suffix=".md", delete=False) as f:
        path = Path(f.name)
        f.write(original)
    try:
        if callable(editor):
            editor(path)
        else:
            command = editor or os.getenv("VISUAL") or os.getenv("EDITOR") or ("notepad" if os.name == "nt" else "vi")
            # On Windows let CreateProcess and the editor's native argv parser handle quoting.
            if os.name == "nt":
                args = command + " " + subprocess.list2cmdline([str(path)])
            else:
                args = [*shlex.split(command), str(path)]
            subprocess.run(args, check=True)
        edited = path.read_bytes()
        response = dict(changed=False, target=target)
        if edited != original:
            parse = parse_ticket_md if is_ticket else parse_note_md
            meta, fields = parse(edited.decode("utf-8-sig"))
            _keys(meta, _TICKET if is_ticket else (*_NOTE, "links"))
            if any(type(meta[k]) is not type(item[k]) or meta[k] != item[k] for k in _IDENTITY):
                raise DomainError("Item identity and version are read-only")
            previous = dict(item, links=links)
            delta = {k: v for k, v in fields.items() if previous[k] != v}
            base = dict(version=item["version"], graph_id=item["graph_id"])
            if is_ticket:
                revision = loaded["revision"] if delta.keys() & {"group_name", "parent_id"} else None
                result = store.patch_ticket(item["id"], delta, revision=revision, **base)
            else:
                result = store.patch_note(item["id"], delta, **base)
            response = dict(changed=True, result=result)
    except DomainError as exc:
        raise DomainError(exc.message, exc.status, **(exc.details | {"draft_path": str(path)})) from exc
    except Exception as exc:
        raise DomainError(f"Could not edit {target}: {exc}", draft_path=str(path)) from exc
    path.unlink()
    return response


def _integer(value, minimum):
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        raise DomainError(f"Expected an integer of at least {minimum}")


def _graph_dirs(root, graphs):
    # Keep portable IDs readable, including when the archive moves to a case-insensitive filesystem.
    if not isinstance(graphs, list):
        raise DomainError("Expected workspace graphs")
    folders, used = {}, set()
    for graph in graphs:
        _keys(graph, _GRAPH)
        key = graph["id"]
        if not isinstance(key, str) or not key.strip() or key in folders:
            raise DomainError("Invalid or duplicate graph ID")
        name = key
        if (re.search(r'[<>:"/\\|?*\x00-\x1f]', key) or key.endswith((".", " ")) or key.startswith("~")
                or PureWindowsPath(key).is_reserved() or len(key.encode()) > 180 or key.casefold() in used):
            name = "~" + sha256(key.encode()).hexdigest()
        used.add(name.casefold())
        folders[key] = root / "graphs" / name
    return folders


def _safe(root, path):
    if not path.resolve().is_relative_to(root):
        raise DomainError(f"Path escapes workspace: {path}")
    return path


def _publish(root, folders, files):
    """Replace archive-owned files, restoring previous bytes if publishing fails."""
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "manifest.yaml"
    previous = []
    if _safe(root, manifest).exists():
        old = _load(manifest.read_bytes().decode("utf-8"))
        _keys(old, ("format_version", "graphs"), ("sequences",))
        previous = list(_graph_dirs(root, old["graphs"]).values())
    stale = set()
    for folder in previous:
        for table, pattern in (("tickets", re.compile(r"[0-9]+")), ("notes", NOTE_ID_RE)):
            directory = _safe(root, folder / table)
            stale.update(p for p in directory.glob("*.md") if pattern.fullmatch(p.stem))
        stale.update(folder / name for name in ("edges.yaml", "anchors.yaml", "proposals.yaml"))
    for folder in folders:
        for table in ("tickets", "notes"):
            _safe(root, folder / table).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as temp, ExitStack() as rollback:
        # The manifest is published last. All renames stay on the destination filesystem.
        for index, path in enumerate([*sorted(stale - files.keys()), *files]):
            _safe(root, path)
            backup = Path(temp) / str(index)
            if path in files:
                staged = backup.with_suffix(".new")
                staged.write_text(files[path], encoding="utf-8", newline="\n")
            if path.is_file():
                path.replace(backup)
                rollback.callback(backup.replace, path)
            if path in files:
                staged.replace(path)
                rollback.callback(path.unlink)
        rollback.pop_all()
    for folder in set(previous) - set(folders):
        for directory in (folder / "tickets", folder / "notes", folder):
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()


def export_workspace(store: Store, out_dir: Path | str, *, graph_id: str | None = None) -> dict:
    root = Path(out_dir).resolve()
    files = {}
    with closing(connect(store.db_path)) as conn:
        conn.execute("BEGIN")
        graphs = _rows(conn, "SELECT * FROM graphs WHERE ? IS NULL OR id=? ORDER BY id", graph_id, graph_id)
        if not graphs and graph_id is not None:
            raise DomainError("Graph not found", 404)
        sequences = dict(conn.execute("SELECT name, seq FROM sqlite_sequence ORDER BY name"))
        folders = _graph_dirs(root, graphs)
        for key, folder in folders.items():
            snap = _snapshot(conn, key)
            links = defaultdict(list)
            for link in snap["note_links"]:
                links[link["source_note_id"]].append(link)
            for ticket in snap["tickets"]:
                files[folder / "tickets" / f"{ticket['id']:04d}.md"] = _ticket_document(ticket, _DATES)
            for note in snap["notes"]:
                files[folder / "notes" / (note["id"] + ".md")] = _note_document(
                    note, links[note["id"]], (*_DATES, "adopted_body_hash"))
            files[folder / "edges.yaml"] = _yaml([[e[k] for k in EDGE_KEY] for e in snap["edges"]])
            files[folder / "anchors.yaml"] = _yaml(snap["anchors"])
            files[folder / "proposals.yaml"] = _yaml(_rows(
                conn, "SELECT rowid, * FROM proposals WHERE graph_id=? ORDER BY id", key))
    files[root / "manifest.yaml"] = _yaml(dict(format_version=1, graphs=graphs, sequences=sequences))
    _publish(root, folders.values(), files)
    return dict(graphs=len(graphs), directory=str(root))


def _archived_document(path, graph_id, table):
    text = path.read_bytes().decode("utf-8")
    links, audit = [], _DATES
    if table == "tickets":
        meta, fields = parse_ticket_md(text)
        _integer(meta["id"], 1)
        filename = f"{meta['id']:04d}.md"
    else:
        meta, fields = parse_note_md(text)
        if not isinstance(meta["id"], str) or not NOTE_ID_RE.fullmatch(meta["id"]):
            raise DomainError("Invalid note ID")
        filename = meta["id"] + ".md"
        audit = (*_DATES, "adopted_body_hash")
        if meta.get("adopted_body_hash") is not None:
            _text_fields(meta, ("adopted_body_hash",))
        for link in fields.pop("links"):
            if link["target_type"] == "note" and link["target_id"] == meta["id"]:
                raise DomainError("Self note link")
            links.append(dict(source_note_id=meta["id"], **link))
    if meta["graph_id"] != graph_id or path.name != filename or set(audit) - meta.keys():
        raise DomainError(f"Invalid archive metadata in {path}")
    _integer(meta["version"], 1)
    _text_fields(meta, _DATES)
    return _select(meta, (*_IDENTITY, *audit)) | fields, links


def _validate_anchor(row, graph_id):
    _keys(row, _ANCHOR)
    _integer(row["id"], 1)
    if row["graph_id"] != graph_id or row["target_type"] not in ("ticket", "note"):
        raise DomainError("Invalid anchor target")
    if row["target_type"] == "ticket":
        _integer(row["target_id"], 1)
    _text_fields(row, ("pinned_sha", "content_hash", "excerpt", "created_at", "verified_at"))
    anchor_spec(row)
    if not re.fullmatch(r"[0-9a-f]{40}([0-9a-f]{24})?", row["pinned_sha"]):
        raise DomainError("Invalid pinned commit")
    if row["kind"] == "file":
        if None in (row["start_line"], row["end_line"]) or not re.fullmatch(r"[0-9a-f]{64}", row["content_hash"]):
            raise DomainError("Invalid pinned file range or content hash")
    elif row["content_hash"] != row["pinned_sha"]:
        raise DomainError("Invalid commit content hash")


def _validate_proposal(row, graph_id):
    _keys(row, _PROPOSAL, ("rowid",))
    if "rowid" in row:
        _integer(row["rowid"], 1)
    _text_fields(row, ("id", "source", "operations_json", "summary", "rationale", "decision_note", "created_at"))
    _text_fields(row, [k for k in ("request_id", "result_json", "decided_at") if row[k] is not None])
    if row["graph_id"] != graph_id or row["status"] not in ("pending", "applied", "rejected", "expired"):
        raise DomainError("Invalid proposal metadata")
    _integer(row["base_revision"], 0)
    batch = json.loads(row["operations_json"])
    _keys(batch, ("operations", "versions"), ("deleted_target",))
    if not isinstance(batch["operations"], list) or not 1 <= len(batch["operations"]) <= 100:
        raise DomainError("Invalid proposal operations")
    for op in batch["operations"]:
        if not isinstance(op, dict) or op.get("op") not in _OP_KEYS:
            raise DomainError("Invalid proposal operation")
        _keys(op, ("op", *_OP_KEYS[op["op"]]))
    if batch["versions"] is not None:
        if not isinstance(batch["versions"], dict):
            raise DomainError("Invalid proposal versions")
        for version in batch["versions"].values():
            _integer(version, 1)
    if row["result_json"] is not None and not isinstance(json.loads(row["result_json"]), dict):
        raise DomainError("Invalid proposal result")


def _read_workspace(root):
    def read(path):
        return _safe(root, path).read_bytes().decode("utf-8")

    def records(folder, name):
        data = _load(read(folder / (name + ".yaml")))
        if not isinstance(data, list):
            raise DomainError(f"Expected a list in {name}.yaml")
        return data

    manifest = _load(read(root / "manifest.yaml"))
    _keys(manifest, ("format_version", "graphs"), ("sequences",))
    if type(manifest["format_version"]) is not int or manifest["format_version"] != 1:
        raise DomainError("Unsupported workspace format")
    sequences = manifest.get("sequences", {})
    _keys(sequences, (), ("tickets", "anchors"))
    tables = defaultdict(list)
    folders = _graph_dirs(root, manifest["graphs"])
    for graph in manifest["graphs"]:
        key = graph["id"]
        folder = folders[key]
        _integer(graph["revision"], 0)
        _text_fields(graph, ("name", *_DATES))
        if not graph["name"].strip():
            raise DomainError("Graph name is required")
        tables["graphs"].append(graph)
        snap = dict(tickets=[], notes=[], note_links=[], edges=[], anchors=records(folder, "anchors"))
        for table in ("tickets", "notes"):
            directory = _safe(root, folder / table)
            if not directory.is_dir():
                raise DomainError(f"Missing workspace directory: {directory}")
            for path in sorted(directory.glob("*.md")):
                row, links = _archived_document(_safe(root, path), key, table)
                snap[table].append(row)
                snap["note_links"].extend(links)
        for edge in records(folder, "edges"):
            if not isinstance(edge, list) or len(edge) != 2:
                raise DomainError("Expected a pair of ticket IDs")
            for value in edge:
                _integer(value, 1)
            snap["edges"].append(dict(zip(EDGE_KEY, edge)))
        for row in snap["anchors"]:
            _validate_anchor(row, key)
        # The reducer checks both DAGs, same-graph targets, relation kinds and inherited groups.
        checked = _Graph(snap).finish()
        if checked["tickets"] != snap["tickets"]:
            raise DomainError("Invalid inherited ticket group")
        for table, rows in snap.items():
            tables["ticket_edges" if table == "edges" else table].extend(rows)
        for row in records(folder, "proposals"):
            _validate_proposal(row, key)
            tables["proposals"].append(row)
    for name, seq in sequences.items():
        _integer(seq, 0)
        if seq < max((r["id"] for r in tables[name]), default=0):
            raise DomainError("Invalid workspace sequence")
    for table, keys in (("tickets", ("id",)), ("notes", ("id",)), ("anchors", ("id",)),
                        ("proposals", ("id",)), ("ticket_edges", EDGE_KEY), ("note_links", LINK_KEY)):
        if len({tuple(r[k] for k in keys) for r in tables[table]}) != len(tables[table]):
            raise DomainError(f"Duplicate {table} record")
    return tables, sequences


def import_workspace(db_path: Path | str, in_dir: Path | str, *, replace: bool = False) -> dict:
    try:
        tables, sequences = _read_workspace(Path(in_dir).resolve())
        with closing(connect(init_db(Path(db_path)))) as conn, write_tx(conn):
            occupied = conn.execute("""SELECT EXISTS(SELECT 1 FROM graphs WHERE id!='default') OR
                EXISTS(SELECT 1 FROM tickets) OR EXISTS(SELECT 1 FROM notes) OR EXISTS(SELECT 1 FROM proposals)""").fetchone()[0]
            if occupied and not replace:
                raise DomainError("Target database is not empty; pass replace=True to overwrite", 409)
            conn.execute("PRAGMA defer_foreign_keys=ON")
            conn.execute("DELETE FROM graphs")
            conn.execute("DELETE FROM sqlite_sequence WHERE name IN ('tickets','anchors')")
            for table in ("graphs", "tickets", "notes", "ticket_edges", "note_links", "anchors", "proposals"):
                for row in tables[table]:
                    _insert(conn, table, row)
            for name, seq in sequences.items():
                conn.execute("DELETE FROM sqlite_sequence WHERE name=?", (name,))
                conn.execute("INSERT INTO sqlite_sequence VALUES (?, ?)", (name, seq))
            rebuild_fts(conn)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.IntegrityError) as exc:
        raise DomainError(f"Invalid workspace: {exc}") from exc
    return {table: len(rows) for table, rows in tables.items()}
