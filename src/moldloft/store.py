"""Transactional graph, search, and review operations backed by one SQLite database."""

from contextlib import closing, contextmanager
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from threading import Event, Lock
from uuid import uuid4

from .config import resolve_db_path
from .db import connect, delete_fts_item, init_db, sync_fts_item, utc_now, write_tx
from .errors import DomainError
from .git_truth import GitTruth
from .reducer import (ANCHOR_FIELDS, EDGE_KEY, LINK_KEY, NOTE_DEFAULTS, NOTE_ID_RE,
                      TICKET_DEFAULTS, enrich, graph_changes, reduce_graph)


def _rows(conn, sql, *args):
    return [dict(row) for row in conn.execute(sql, args)]


def _one(conn, sql, *args):
    row = conn.execute(sql, args).fetchone()
    if row is None:
        raise DomainError("Item or graph not found", 404)
    return dict(row)


def _graph(conn, graph_id):
    return _one(conn, "SELECT * FROM graphs WHERE id=?", graph_id)


def _snapshot(conn, graph_id):
    graph = _graph(conn, graph_id)
    for table in ("tickets", "notes", "anchors"):
        graph[table] = _rows(conn, f"SELECT * FROM {table} WHERE graph_id=? ORDER BY id", graph_id)
    graph["edges"] = _rows(conn, """SELECT e.* FROM ticket_edges e JOIN tickets t ON t.id=e.blocker_id
        WHERE t.graph_id=? ORDER BY blocker_id, blocked_id""", graph_id)
    graph["note_links"] = _rows(conn, """SELECT l.* FROM note_links l JOIN notes n ON n.id=l.source_note_id
        WHERE n.graph_id=? ORDER BY source_note_id, relation, target_type, target_id""", graph_id)
    for row in graph["notes"]:
        row["in_graph"] = bool(row["in_graph"])
    for row in graph["note_links"] + graph["anchors"]:
        if row["target_type"] == "ticket":
            row["target_id"] = int(row["target_id"])
    return graph


def _target(value):
    text = str(value)
    if re.fullmatch(r"(ticket:)?[0-9]{1,19}", text):
        key = int(text.removeprefix("ticket:"))
        if 0 < key <= 2**63 - 1:
            return "ticket", key
    key = text.removeprefix("note:")
    if NOTE_ID_RE.fullmatch(key):
        return "note", key
    raise DomainError("Invalid item ID")


def _limit(value):
    if type(value) is not int or not 1 <= value <= 1000:
        raise DomainError("Limit must be between 1 and 1000")
    return value


def _label(value, name):
    if not isinstance(value, str) or not value.strip():
        raise DomainError(f"{name} is required")
    return value.strip()


def _bump(conn, graph_id, now):
    conn.execute("UPDATE graphs SET revision=revision+1, updated_at=? WHERE id=?", (now, graph_id))


def _insert(conn, table, values):
    # Column names come exclusively from schema-owned field lists.
    columns = ", ".join(values)
    params = ", ".join("?" for _ in values)
    return conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({params})", tuple(values.values())).lastrowid


def _update(conn, table, key, values):
    setters = ", ".join(f"{k}=?" for k in values)
    conn.execute(f"UPDATE {table} SET {setters} WHERE id=?", (*values.values(), key))


def _item_index(graph):
    return {(kind, row["id"]): row for kind in ("ticket", "note") for row in graph[kind + "s"]}


def _check_versions(graph, versions):
    if not isinstance(versions, dict):
        raise DomainError("Expected an item version map")
    items = _item_index(graph)
    for key, version in versions.items():
        row = items.get(_target(key))
        if type(version) is not int:
            raise DomainError("Expected an integer item version")
        if not row or row["version"] != version:
            raise DomainError(f"Version conflict for {key}", 409, current=row)


def _check_revision(graph, revision):
    if type(revision) is not int:
        raise DomainError("Expected an integer graph revision")
    if graph["revision"] != revision:
        raise DomainError("Graph revision conflict", 409, current=graph["revision"])


def _sync_relations(conn, table, keys, old, new):
    def identities(rows):
        return {tuple(row[k] for k in keys) for row in rows}
    before, after = identities(old), identities(new)
    where = " AND ".join(f"{k}=?" for k in keys)
    params = ",".join("?" for _ in keys)
    conn.executemany(f"DELETE FROM {table} WHERE {where}", before - after)
    conn.executemany(f"INSERT INTO {table} VALUES ({params})", after - before)
    return before ^ after


def _persist(conn, before, reduced):
    """Allocate real IDs, translate references once, then write only changed rows."""
    graph = reduced["preview"]
    graph_id, now = before["id"], utc_now()
    created, versions, anchors_created = {}, {}, []
    old = _item_index(before)
    fresh = set()
    touched = {("note", op["note_id"]) for op in reduced["operations"] if op["op"] == "note_delete"}
    for kind, fields in (("ticket", TICKET_DEFAULTS), ("note", (*NOTE_DEFAULTS, "adopted_body_hash"))):
        for row in graph[kind + "s"]:
            key = row["id"]
            if (kind, key) in old:
                continue
            values = {k: row[k] for k in fields}
            if kind == "ticket":
                values["parent_id"] = None  # A parent may be created later in this batch.
            else:
                values["id"] = key
            new_id = _insert(conn, kind + "s", dict(values, graph_id=graph_id, created_at=now, updated_at=now))
            row["id"] = created[key] = new_id if kind == "ticket" else key
            fresh.add((kind, row["id"]))
    for row in graph["tickets"]:
        row["parent_id"] = created.get(row["parent_id"], row["parent_id"])
    for row in graph["edges"]:
        for key in EDGE_KEY:
            row[key] = created.get(row[key], row[key])
    for row in graph["note_links"] + graph["anchors"]:
        row["target_id"] = created.get(row["target_id"], row["target_id"])
    items = _item_index(graph)
    for edge in _sync_relations(conn, "ticket_edges", EDGE_KEY, before["edges"], graph["edges"]):
        touched.update(("ticket", k) for k in edge)
    for source, _, _, _ in _sync_relations(conn, "note_links", LINK_KEY, before["note_links"], graph["note_links"]):
        touched.add(("note", source))
    old_anchors = {a["id"]: a for a in before["anchors"]}
    edited = {op["anchor_id"] for op in reduced["operations"] if op["op"] == "anchor_update"}
    for row in graph["anchors"]:
        key = row["id"]
        existing = old_anchors.pop(key, None)
        if existing and key not in edited:
            continue
        values = {k: row[k] for k in (*ANCHOR_FIELDS, "pinned_sha", "content_hash", "excerpt")}
        values["verified_at"] = now
        if existing:
            _update(conn, "anchors", key, values)
        else:
            anchors_created.append(_insert(conn, "anchors", dict(values, graph_id=graph_id, created_at=now,
                                                                  target_type=row["target_type"], target_id=row["target_id"])))
        touched.add((row["target_type"], row["target_id"]))
    for key, row in old_anchors.items():
        conn.execute("DELETE FROM anchors WHERE id=?", (key,))
        touched.add((row["target_type"], row["target_id"]))
    for (kind, key), row in items.items():
        fields = TICKET_DEFAULTS if kind == "ticket" else (*NOTE_DEFAULTS, "adopted_body_hash")
        original = old.get((kind, key), {})
        changed = {k: row[k] for k in fields if row[k] != original.get(k)}
        if (kind, key) in fresh:
            version = 1
            if kind == "ticket" and row["parent_id"] is not None:
                _update(conn, "tickets", key, {"parent_id": row["parent_id"]})
        elif changed or (kind, key) in touched:
            version = original["version"] + 1
            _update(conn, kind + "s", key, dict(changed, version=version, updated_at=now))
        else:
            continue
        versions[f"{kind}:{key}"] = version
        summary, body = ("description", "tech_notes") if kind == "ticket" else ("summary", "body")
        if (kind, key) in fresh or changed.keys() & {"title", summary, body}:
            sync_fts_item(conn, graph_id, kind, key, row["title"], row[summary], row[body])
    deleted = old.keys() - items.keys()
    for kind, key in deleted:
        conn.execute(f"DELETE FROM {kind}s WHERE id=?", (key,))
        delete_fts_item(conn, kind, key)
    # Named notes can be recreated at version 1. Remember the loss of identity
    # for pending field edits while keeping their submitted operations intact.
    deleted_notes = {f"note:{key}" for kind, key in deleted if kind == "note"}
    if deleted_notes:
        for row in _rows(conn, "SELECT id, operations_json FROM proposals WHERE graph_id=? AND status='pending'", graph_id):
            batch = json.loads(row["operations_json"])
            lost = deleted_notes.intersection(batch["versions"] or {})
            if lost:
                batch["deleted_target"] = min(lost)
                _update(conn, "proposals", row["id"], {"operations_json": json.dumps(batch)})
    _bump(conn, graph_id, now)
    return dict(graph_id=graph_id, revision=before["revision"] + 1, created=created,
                versions=versions, anchors_created=anchors_created)


class Store:
    def __init__(self, db_path: Path | str, git_truth: GitTruth | None = None):
        self.db_path = init_db(resolve_db_path(db_path))
        self.git_truth = git_truth
        self._listeners = set()
        self._lock = Lock()

    @contextmanager
    def subscribe(self):
        """Yield a threading.Event that wakes after commits to this store."""
        event = Event()
        with self._lock:
            self._listeners.add(event)
        try:
            yield event
        finally:
            with self._lock:
                self._listeners.discard(event)

    @contextmanager
    def _read(self):
        with closing(connect(self.db_path)) as conn:
            conn.execute("BEGIN")
            yield conn

    @contextmanager
    def _write(self):
        with closing(connect(self.db_path)) as conn:
            try:
                with write_tx(conn):
                    yield conn
            except sqlite3.IntegrityError as exc:
                raise DomainError(f"Conflicting graph data: {exc}", 409) from exc
            if conn.total_changes:
                with self._lock:
                    for event in self._listeners:
                        event.set()

    def list_graphs(self):
        with self._read() as conn:
            return _rows(conn, "SELECT * FROM graphs ORDER BY name")

    def graph_summaries(self):
        with self._read() as conn:
            return _rows(conn, """SELECT id, name, revision,
                (SELECT count(*) FROM tickets WHERE graph_id=g.id) AS ticket_count,
                (SELECT count(*) FROM notes WHERE graph_id=g.id) AS note_count,
                (SELECT count(*) FROM proposals WHERE graph_id=g.id AND status='pending') AS pending_proposals
                FROM graphs g ORDER BY name""")

    def event_state(self, graph_id):
        with self._read() as conn:
            revision = _graph(conn, graph_id)["revision"]
            pending = conn.execute("""SELECT id FROM proposals WHERE graph_id=? AND status='pending'
                                      ORDER BY id""", (graph_id,))
            signature = sha256()
            for row in pending:
                signature.update(row["id"].encode())
            return dict(revision=revision, pending_sig=signature.hexdigest())

    def get_graph(self, graph_id):
        with self._read() as conn:
            return _graph(conn, graph_id)

    def create_graph(self, name, graph_id=None):
        name = _label(name, "Graph name")
        graph_id = uuid4().hex if graph_id is None else _label(graph_id, "Graph ID")
        with self._write() as conn:
            now = utc_now()
            conn.execute("INSERT INTO graphs VALUES (?, ?, 0, ?, ?)", (graph_id, name, now, now))
            return _graph(conn, graph_id)

    def rename_graph(self, graph_id, name):
        with self._write() as conn:
            conn.execute("UPDATE graphs SET name=? WHERE id=?", (_label(name, "Graph name"), graph_id))
            _bump(conn, graph_id, utc_now())
            return _graph(conn, graph_id)

    def delete_graph(self, graph_id):
        if graph_id == "default":
            raise DomainError("Cannot delete the default graph", 409)
        with self._write() as conn:
            _graph(conn, graph_id)
            conn.execute("DELETE FROM search_fts WHERE graph_id=?", (graph_id,))
            conn.execute("DELETE FROM graphs WHERE id=?", (graph_id,))

    def move_ticket(self, ticket_id, target_graph_id):
        kind, ticket_id = _target(ticket_id)
        if kind != "ticket":
            raise DomainError("Expected a ticket")
        with self._write() as conn:
            source = _one(conn, "SELECT graph_id FROM tickets WHERE id=?", ticket_id)["graph_id"]
            _graph(conn, target_graph_id)
            if source == target_graph_id:
                raise DomainError("Ticket already belongs to this graph")
            rows = _rows(conn, """WITH RECURSIVE subtree(id) AS (
                SELECT id FROM tickets WHERE id=? UNION ALL
                SELECT t.id FROM tickets t JOIN subtree s ON t.parent_id=s.id)
                SELECT id FROM subtree ORDER BY id""", ticket_id)
            moved = {r["id"] for r in rows}
            edges = _rows(conn, """SELECT e.* FROM ticket_edges e JOIN tickets t ON t.id=e.blocker_id
                WHERE t.graph_id=? ORDER BY blocker_id, blocked_id""", source)
            crossing = [e for e in edges if (e["blocker_id"] in moved) != (e["blocked_id"] in moved)]
            if crossing:
                pairs = ", ".join(f"{e['blocker_id']} -> {e['blocked_id']}" for e in crossing)
                raise DomainError(f"Cross-graph dependencies prevent move: {pairs}", 409, edges=crossing)
            now = utc_now()
            notes = set()
            for key in moved:
                notes.update(r[0] for r in conn.execute(
                    "SELECT source_note_id FROM note_links WHERE target_type='ticket' AND target_id=?", (str(key),)))
                conn.execute("DELETE FROM note_links WHERE target_type='ticket' AND target_id=?", (str(key),))
                conn.execute("UPDATE anchors SET graph_id=? WHERE target_type='ticket' AND target_id=?",
                             (target_graph_id, str(key)))
                conn.execute("UPDATE search_fts SET graph_id=? WHERE item_type='ticket' AND item_id=?",
                             (target_graph_id, str(key)))
                conn.execute("UPDATE tickets SET graph_id=?, version=version+1, updated_at=? WHERE id=?",
                             (target_graph_id, now, key))
            conn.execute("UPDATE tickets SET parent_id=NULL WHERE id=?", (ticket_id,))
            conn.executemany("UPDATE notes SET version=version+1, updated_at=? WHERE id=?", [(now, n) for n in notes])
            _bump(conn, source, now)
            _bump(conn, target_graph_id, now)
            return dict(graph_id=target_graph_id, moved=sorted(moved))

    def revision(self, graph_id="default"):
        return self.get_graph(graph_id)["revision"]

    def snapshot(self, graph_id="default", *, include_pending=False):
        with self._read() as conn:
            graph = _snapshot(conn, graph_id)
            if include_pending:
                graph["pending_proposals"] = conn.execute(
                    "SELECT count(*) FROM proposals WHERE graph_id=? AND status='pending'", (graph_id,)).fetchone()[0]
            return enrich(graph)

    def get_item(self, target, graph_id=None):
        kind, key = _target(target)
        with self._read() as conn:
            if graph_id is None:
                graph_id = _one(conn, f"SELECT graph_id FROM {kind}s WHERE id=?", key)["graph_id"]
            graph = enrich(_snapshot(conn, graph_id))
        items = _item_index(graph)
        if (kind, key) not in items:
            raise DomainError("Item not found in graph", 404)
        relations = [(("ticket", t["parent_id"]), "contains", ("ticket", t["id"]))
                     for t in graph["tickets"] if t["parent_id"] is not None]
        relations.extend((("ticket", e["blocker_id"]), "blocks", ("ticket", e["blocked_id"])) for e in graph["edges"])
        relations.extend((("note", l["source_note_id"]), l["relation"], (l["target_type"], l["target_id"]))
                         for l in graph["note_links"])
        neighbors = []
        for a, relation, b in relations:
            if (kind, key) in (a, b):
                other = b if a == (kind, key) else a
                neighbors.append(dict(target_type=other[0], target_id=other[1], title=items[other]["title"],
                                      relation=relation, direction="out" if other == b else "in"))
        return dict(graph_id=graph_id, revision=graph["revision"], item=items[kind, key], neighbors=neighbors,
                    anchors=[a for a in graph["anchors"] if (a["target_type"], a["target_id"]) == (kind, key)])

    def get_ready_work(self, graph_id="default", limit=50):
        return [t for t in self.snapshot(graph_id)["tickets"] if t["ready"]][:_limit(limit)]

    def search(self, query, graph_id="default", kind=None, limit=30):
        if not isinstance(query, str) or kind not in (None, "ticket", "note"):
            raise DomainError("Invalid search")
        limit = _limit(limit)
        cleaned = query.strip()
        terms = re.findall(r"\w+", cleaned)
        if not cleaned:
            return []
        match = " AND ".join(f'"{word}"*' for word in terms) if terms else None
        like = "%" + cleaned.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        try:
            exact_kind, key = _target(cleaned)
        except DomainError:
            exact_kind, key = None, None
        with self._read() as conn:
            _graph(conn, graph_id)
            if match:
                return _rows(conn, """WITH hits AS (
                    SELECT rowid, rank AS score FROM search_fts WHERE search_fts MATCH ?
                    UNION ALL SELECT rowid, -1e30 FROM search_fts WHERE item_type=? AND item_id=?
                    UNION ALL SELECT rowid, 1e6 FROM search_fts
                        WHERE title LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\')
                    SELECT f.item_type, f.item_id, f.title, f.summary FROM hits JOIN search_fts f ON f.rowid=hits.rowid
                    WHERE f.graph_id=? AND (? IS NULL OR f.item_type=?) GROUP BY f.rowid
                    ORDER BY min(score), f.rowid LIMIT ?""",
                    match, exact_kind, str(key), like, like, like, graph_id, kind, kind, limit)
            return _rows(conn, """SELECT item_type, item_id, title, summary FROM search_fts
                WHERE graph_id=? AND (? IS NULL OR item_type=?)
                  AND (title LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\')
                ORDER BY rowid LIMIT ?""", graph_id, kind, kind, like, like, like, limit)

    def _preview(self, conn, before, operations):
        reduced = reduce_graph(before, operations)
        for op in reduced["operations"]:
            if op["op"] == "note_create" and conn.execute(
                    "SELECT 1 FROM notes WHERE id=? AND graph_id<>?", (op["id"], before["id"])).fetchone():
                raise DomainError(f"Note ID {op['id']} already belongs to another graph", 409)
        edited = {op["anchor_id"] for op in reduced["operations"] if op["op"] == "anchor_update"}
        pinned = False
        for anchor in reduced["preview"]["anchors"]:
            if isinstance(anchor["id"], str) or anchor["id"] in edited:
                if self.git_truth is None:
                    raise DomainError("Configure a Git repository to pin an anchor")
                anchor.update(self.git_truth.pin(anchor))
                pinned = True
        if pinned:
            reduced["changes"] = graph_changes(before, reduced["preview"])
        return reduced

    def mutate(self, operations, *, graph_id="default", revision=None, versions=None):
        with self._write() as conn:
            before = _snapshot(conn, graph_id)
            if revision is not None:
                _check_revision(before, revision)
            if versions is not None:
                _check_versions(before, versions)
            return _persist(conn, before, self._preview(conn, before, operations))

    def patch_ticket(self, ticket_id, fields, *, version, revision=None, graph_id="default"):
        if isinstance(fields, dict) and fields.keys() & {"group_name", "parent_id"} and revision is None:
            raise DomainError("Structural ticket edits require a graph revision", 409)
        return self.mutate([dict(op="ticket_update", ticket_id=ticket_id, fields=fields)], graph_id=graph_id,
                           revision=revision, versions={f"ticket:{ticket_id}": version})

    def patch_note(self, note_id, fields, *, version, graph_id="default"):
        return self.mutate([dict(op="note_update", note_id=note_id, fields=fields)], graph_id=graph_id,
                           versions={f"note:{note_id}": version})

    def verify_ground_truth(self, graph_id="default", target=None):
        anchors = self.snapshot(graph_id)["anchors"] if target is None else self.get_item(target, graph_id)["anchors"]
        return (self.git_truth or GitTruth()).verify_many(anchors)

    def reanchor(self, anchor_id, *, ref="HEAD", start_line=None, end_line=None, symbol=None, claim=None):
        with self._write() as conn:
            row = _one(conn, "SELECT * FROM anchors WHERE id=?", anchor_id)
            before = _snapshot(conn, row["graph_id"])
            fields = dict(ref=row["ref"] if row["kind"] == "branch" and ref == "HEAD" else ref)
            fields.update({k: v for k, v in dict(start_line=start_line, end_line=end_line,
                                                 symbol=symbol, claim=claim).items() if v is not None})
            op = dict(op="anchor_update", anchor_id=anchor_id, anchor=fields)
            return _persist(conn, before, self._preview(conn, before, [op]))

    def submit_proposal(self, operations, *, base_revision, summary, rationale, graph_id="default",
                        request_id=None, source="mcp"):
        summary, rationale = _label(summary, "Summary"), _label(rationale, "Rationale")
        source = _label(source, "Source")
        if request_id is not None:
            request_id = _label(request_id, "Request ID")
        with self._write() as conn:
            if request_id is not None:
                existing = conn.execute("SELECT * FROM proposals WHERE request_id=?", (request_id,)).fetchone()
                if existing:
                    record = _proposal(existing)
                    payload = (operations, base_revision, graph_id, summary, rationale, source)
                    saved = [record[k] for k in ("operations", "base_revision", "graph_id", "summary", "rationale", "source")]
                    if json.dumps(payload, sort_keys=True) != json.dumps(saved, sort_keys=True):
                        raise DomainError("Request ID already used for a different proposal", 409)
                    return record
            before = _snapshot(conn, graph_id)
            _check_revision(before, base_revision)
            self._preview(conn, before, operations)
            base_versions = _proposal_versions(before, operations)
            key = uuid4().hex
            batch = json.dumps(dict(operations=operations, versions=base_versions))
            _insert(conn, "proposals", dict(id=key, graph_id=graph_id, request_id=request_id, source=source,
                                            base_revision=base_revision, operations_json=batch, summary=summary,
                                            rationale=rationale, created_at=utc_now()))
            return _get_proposal(conn, key)

    def list_proposals(self, graph_id="default", status="pending", limit=50):
        status = None if status == "all" else status
        with self._read() as conn:
            before = _snapshot(conn, graph_id)
            rows = _rows(conn, """SELECT * FROM proposals WHERE graph_id=? AND (? IS NULL OR status=?)
                ORDER BY created_at DESC, rowid DESC LIMIT ?""", graph_id, status, status, _limit(limit))
            records = [_proposal(row) for row in rows]
            for record in records:
                record["applicable"] = _conflict(record, before) is None
                # Recheck inputs that can change outside this graph's revision.
                external = any(op["op"] in ("note_create", "anchor_add", "anchor_update") for op in record["operations"])
                if record["applicable"] and external:
                    try:
                        self._preview(conn, before, record["operations"])
                    except DomainError:
                        record["applicable"] = False
            return records

    def inspect_proposal(self, proposal_id):
        with self._read() as conn:
            record = _get_proposal(conn, proposal_id)
            graph = _snapshot(conn, record["graph_id"])
            reason = _conflict(record, graph)
            try:
                preview = self._preview(conn, graph, record["operations"])
            except DomainError as exc:
                return dict(record, applicable=False, reason=reason or exc.message, preview=None, changes=[])
            return dict(record, applicable=reason is None, reason=reason, preview=preview["preview"],
                        changes=preview["changes"])

    def get_proposal(self, proposal_id):
        with self._read() as conn:
            return _get_proposal(conn, proposal_id)

    def apply_proposal(self, proposal_id, *, edits=None, note=""):
        """Review edits take the form [{"op_index": 0, "fields": {"title": "..."}}]."""
        if not isinstance(note, str):
            raise DomainError("Decision note must be text")
        with self._write() as conn:
            record = _get_proposal(conn, proposal_id)
            _pending(record)
            before = _snapshot(conn, record["graph_id"])
            conflict = _conflict(record, before)
            if conflict:
                conn.execute("UPDATE proposals SET status='expired', decision_note=?, decided_at=? WHERE id=?",
                             (conflict, utc_now(), proposal_id))
            else:
                amended = _amend(record["operations"], edits)
                result = _persist(conn, before, self._preview(conn, before, amended))
                conn.execute("""UPDATE proposals SET status='applied', decision_note=?, result_json=?, decided_at=?
                    WHERE id=?""", (note, json.dumps(dict(result, operations=amended)), utc_now(), proposal_id))
                return _get_proposal(conn, proposal_id)
        # Let the explicit expiration commit before returning the conflict to the reviewer.
        raise DomainError(conflict, 409)

    def reject_proposal(self, proposal_id, *, reason):
        reason = _label(reason, "Rejection reason")
        with self._write() as conn:
            _pending(_get_proposal(conn, proposal_id))
            conn.execute("UPDATE proposals SET status='rejected', decision_note=?, decided_at=? WHERE id=?",
                         (reason, utc_now(), proposal_id))
            return _get_proposal(conn, proposal_id)

    def clear_proposals(self, graph_id="default", statuses=("applied", "rejected", "expired")):
        if not isinstance(statuses, (tuple, list)) or any(s not in ("applied", "rejected", "expired") for s in statuses):
            raise DomainError("Only decided proposals may be cleared")
        with self._write() as conn:
            _graph(conn, graph_id)
            return sum(conn.execute("DELETE FROM proposals WHERE graph_id=? AND status=?", (graph_id, s)).rowcount
                       for s in statuses)


def _proposal(row):
    record = dict(row)
    batch = json.loads(record.pop("operations_json"))
    result = record.pop("result_json")
    return dict(record, operations=batch["operations"], base_versions=batch["versions"], deleted_target=batch.get("deleted_target"),
                result=json.loads(result) if result else None)


def _get_proposal(conn, key):
    return _proposal(_one(conn, "SELECT * FROM proposals WHERE id=?", key))


def _pending(record):
    if record["status"] != "pending":
        raise DomainError(f"Proposal already {record['status']}", 409)


def _proposal_versions(graph, operations):
    items, versions = _item_index(graph), {}
    for op in operations:
        if op["op"] not in ("ticket_update", "note_update") or {"parent_id", "group_name", "links"} & op["fields"].keys():
            return None
        kind = op["op"].split("_")[0]
        key = f"{kind}:{op[kind + '_id']}"
        versions[key] = items[_target(key)]["version"]
    return versions


def _conflict(record, graph):
    if record["status"] != "pending":
        return f"Proposal already {record['status']}"
    if record["deleted_target"]:
        return f"Proposed target {record['deleted_target']} was deleted"
    if graph["revision"] == record["base_revision"]:
        return None
    if record["base_versions"] is None:
        return "Structural proposal requires its original graph revision"
    try:
        _check_versions(graph, record["base_versions"])
    except DomainError as exc:
        return exc.message
    return None


def _amend(operations, edits):
    if edits is None:
        return operations
    if not isinstance(edits, list):
        raise DomainError("Reviewer edits must be a list")
    amended = deepcopy(operations)
    for edit in edits:
        if not isinstance(edit, dict) or edit.keys() != {"op_index", "fields"}:
            raise DomainError("Expected op_index and fields in reviewer edit")
        index, fields = edit["op_index"], edit["fields"]
        if type(index) is not int or not 0 <= index < len(amended) or not isinstance(fields, dict):
            raise DomainError("Invalid reviewer edit")
        op = amended[index]
        proposed = op.get("fields", op.get("anchor", {}))
        allowed = {"title", "description", "tech_notes", "group_name", "summary", "body", "claim", "symbol"}
        if fields.keys() - (allowed & proposed.keys()) or not all(isinstance(v, str) for v in fields.values()):
            raise DomainError("Only proposed text fields may be amended")
        proposed.update(fields)
    return amended
