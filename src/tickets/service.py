"""Graph transactions shared by forms, the JSON API and Copilot.

Preview and apply use the same pure reducer. BEGIN IMMEDIATE serializes cycle
validation with writes. No transcript or unaccepted proposal is persisted.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
import time

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .writes import transaction

from .models import Ticket, TicketEdge, CodeLink, GraphMeta, MutationReceipt, STATUSES, PRIORITIES

FIELDS = {"title", "description", "tech_notes", "status", "priority", "group_name", "parent_id"}
LIMITS = {"title": 200, "description": 20000, "tech_notes": 20000, "group_name": 80}
LINK_FIELDS = {"repo", "kind", "ref", "path", "start_line", "end_line"}


class DomainError(Exception):
    def __init__(self, message, status=422, **details):
        super().__init__(message)
        self.message, self.status, self.details = message, status, details


def row_dict(row):
    return {c.name: (v.isoformat() if isinstance(v, dt.datetime) else v)
            for c in row.__table__.columns for v in [getattr(row, c.name)]}


def snapshot(session):
    tickets = [row_dict(t) for t in session.scalars(select(Ticket).order_by(Ticket.id))]
    edges = [row_dict(e) for e in session.scalars(select(TicketEdge).order_by(TicketEdge.blocker_id, TicketEdge.blocked_id))]
    links = [row_dict(l) for l in session.scalars(select(CodeLink).order_by(CodeLink.id))]
    return readiness({"revision": session.get(GraphMeta, 1).revision, "tickets": tickets, "edges": edges, "links": links})


def readiness(graph):
    by_id = {t["id"]: t for t in graph["tickets"]}
    for ticket in graph["tickets"]:
        blockers = [e["blocker_id"] for e in graph["edges"] if e["blocked_id"] == ticket["id"]
                    and by_id[e["blocker_id"]]["status"] != "done"]
        ticket["blockers"] = blockers
        ticket["ready"] = ticket["status"] == "open" and not blockers
    return graph


def cycle_path(nodes, pairs):
    adjacency = {n: [] for n in nodes}
    for source, target in pairs:
        adjacency[source].append(target)
    finished = set()
    for root in nodes:
        if root in finished:
            continue
        path, active, stack = [root], {root: 0}, [iter(adjacency[root])]
        while stack:
            node = next(stack[-1], None)
            if node is None:
                finished.add(path.pop())
                active = {n: i for i, n in enumerate(path)}
                stack.pop()
            elif node in active:
                return path[active[node]:] + [node]
            elif node not in finished:
                active[node] = len(path)
                path.append(node)
                stack.append(iter(adjacency[node]))
    return []


def reduce_graph(before, operations):
    if not isinstance(operations, list) or not 1 <= len(operations) <= 100:
        raise DomainError("Provide between 1 and 100 operations")
    graph = copy.deepcopy(before)
    tickets = {t["id"]: t for t in graph["tickets"]}
    edges = {(e["blocker_id"], e["blocked_id"]) for e in graph["edges"]}
    links = graph["links"]

    def require(ticket_id):
        if not isinstance(ticket_id, (int, str)) or isinstance(ticket_id, bool) or ticket_id not in tickets:
            raise DomainError("Ticket not found", 404)
        return tickets[ticket_id]

    for i, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise DomainError("Invalid operation")
        kind = operation.get("op")
        if kind in ("note_create", "note_update"):
            continue
        if kind in ("create", "update"):
            fields = operation.get("fields", {})
            if not isinstance(fields, dict) or set(fields) - FIELDS:
                raise DomainError("Unknown ticket fields")
            if kind == "create":
                ticket_id = operation.get("temp_id", f"new:{i}")
                if not isinstance(ticket_id, str) or not re.fullmatch(r"new:[A-Za-z0-9_-]{1,40}", ticket_id) or ticket_id in tickets:
                    raise DomainError("New tickets require a unique new:identifier")
                ticket = {"id": ticket_id, "title": "", "description": "", "tech_notes": "",
                          "group_name": "", "parent_id": None, "status": "open", "priority": "medium"}
                tickets[ticket_id] = ticket
            else:
                ticket = require(operation.get("ticket_id"))
            for field, value in fields.items():
                if field in LIMITS:
                    if not isinstance(value, str) or len(value) > LIMITS[field]:
                        raise DomainError(f"Invalid {field}")
                    value = value.strip() if field in ("title", "group_name") else value
                if field == "status" and (not isinstance(value, str) or value not in STATUSES):
                    raise DomainError("Invalid status")
                if field == "priority" and (not isinstance(value, str) or value not in PRIORITIES):
                    raise DomainError("Invalid priority")
                if field == "parent_id" and value is not None:
                    require(value)
                ticket[field] = value
            if not ticket["title"]:
                raise DomainError("Title is required")
        elif kind == "delete":
            ticket = require(operation.get("ticket_id"))
            ticket_id = ticket["id"]
            for child in tickets.values():
                if child["parent_id"] == ticket_id:
                    child["parent_id"] = ticket["parent_id"]
            edges = {(a, b) for a, b in edges if ticket_id not in (a, b)}
            links[:] = [link for link in links if link["ticket_id"] != ticket_id]
            del tickets[ticket_id]
        elif kind in ("edge_add", "edge_remove"):
            a, b = operation.get("blocker_id"), operation.get("blocked_id")
            require(a)
            require(b)
            if kind == "edge_add":
                if a == b or (a, b) in edges:
                    raise DomainError("Self or duplicate dependency", 409)
                edges.add((a, b))
            else:
                if (a, b) not in edges:
                    raise DomainError("Dependency not found", 404)
                edges.remove((a, b))
        elif kind == "link_add":
            ticket = require(operation.get("ticket_id"))
            link = operation.get("link")
            if not isinstance(link, dict) or set(link) - LINK_FIELDS:
                raise DomainError("Invalid code link")
            links.append({**link, "id": f"new:link{i}", "ticket_id": ticket["id"]})
        elif kind == "link_remove":
            match = next((l for l in links if l["id"] == operation.get("link_id")), None)
            if match is None:
                raise DomainError("Code link not found", 404)
            links.remove(match)
        else:
            raise DomainError("Unknown operation")

    parents = [(t["parent_id"], t["id"]) for t in tickets.values() if t["parent_id"] is not None]
    for parent, child in parents:
        require(parent)
    for label, pairs in (("Parent", parents), ("Dependency", edges)):
        cycle = cycle_path(tickets, pairs)
        if cycle:
            raise DomainError(f"{label} cycle", 409, path=cycle)
    for ticket in tickets.values():
        root = ticket
        while root["parent_id"] is not None:
            root = tickets[root["parent_id"]]
        ticket["group_name"] = root["group_name"]
    graph["tickets"] = list(tickets.values())
    def edge_key(edge):
        return tuple((isinstance(i, str), i) for i in edge)
    graph["edges"] = [{"blocker_id": a, "blocked_id": b} for a, b in sorted(edges, key=edge_key)]
    graph = readiness(graph)
    if any(op.get("op") in ("note_create", "note_update") for op in operations):
        from .note_ops import reduce_notes
        graph = reduce_notes(before, graph, operations)
    return graph


def changes_between(before, after):
    changes = []
    a, b = ({t["id"]: t for t in g["tickets"]} for g in (before, after))
    for ticket_id in sorted(a.keys() | b.keys(), key=str):
        old, new = a.get(ticket_id), b.get(ticket_id)
        if old is None or new is None:
            changes.append({"kind": "created" if old is None else "deleted", "ticket_id": ticket_id,
                            "before": {k: old[k] for k in FIELDS} if old else None,
                            "after": {k: new[k] for k in FIELDS} if new else None})
        else:
            fields = [f for f in sorted(FIELDS) if old[f] != new[f]]
            if fields:
                changes.append({"kind": "updated", "ticket_id": ticket_id,
                                "before": {f: old[f] for f in fields}, "after": {f: new[f] for f in fields}})
    for name in ("edges", "links"):
        if before[name] != after[name]:
            changes.append({"kind": name, "before": before[name], "after": after[name]})
    if "notes" in before or "notes" in after:
        from .notes import FIELDS as NOTE_FIELDS
        old_notes, new_notes = ({n['id']: n for n in g.get('notes', [])} for g in (before, after))
        for note_id in sorted(old_notes.keys() | new_notes.keys()):
            old_note, new_note = old_notes.get(note_id), new_notes.get(note_id)
            old_fields = {k: old_note.get(k, '') for k in NOTE_FIELDS} if old_note else None
            new_fields = {k: new_note.get(k, '') for k in NOTE_FIELDS} if new_note else None
            if old_fields != new_fields:
                changes.append({'kind': 'note', 'note_id': note_id, 'before': old_fields, 'after': new_fields})
    return changes


def ticket_id_map(tickets, next_id):
    """Use final reducer order for both durable recovery planning and SQL allocation."""
    aliases = [t['id'] for t in tickets if isinstance(t['id'], str)]
    return {alias: next_id + index for index, alias in enumerate(aliases)}


class Store:
    def __init__(self, engine, git=None, notes=None):
        self.engine, self.git, self.notes = engine, git, notes

    def graph(self):
        with self.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN")
            with Session(connection) as session:
                graph = snapshot(session)
        return self.notes.decorate(graph) if self.notes else graph

    def revision(self):
        with self.engine.connect() as connection:
            return connection.execute(select(GraphMeta.revision).where(GraphMeta.id == 1)).scalar_one()

    def normalize(self, operations):
        operations = copy.deepcopy(operations)
        if isinstance(operations, list):
            for i, operation in enumerate(operations):
                if isinstance(operation, dict) and operation.get("op") == "create":
                    operation.setdefault("temp_id", f"new:{i}")
                if isinstance(operation, dict) and operation.get("op") == "link_add":
                    if self.git is None:
                        raise DomainError("Git integration unavailable", 503)
                    operation["link"] = self.git.normalize_link(operation.get("link", {}))
        return operations

    def preview(self, operations, base=None):
        before = base if base is not None else self.graph()
        operations = self.normalize(operations)
        after = reduce_graph(before, operations)
        if self.notes:
            after = self.notes.decorate(after, {k: after[k] for k in ("notes", "notes_revision", "notes_errors")})
        return {"operations": operations, "base_revision": before["revision"],
                "changes": changes_between(before, after), "preview": after}

    def mutate(self, operations, *, revision=None, versions=None, request_id=None, notes_revision=None):
        if isinstance(operations, list) and any(isinstance(op, dict) and
                op.get('op') in ('note_create', 'note_update') for op in operations):
            raise DomainError('Note operations require reviewed proposal apply; use the single-note editor for human saves')
        digest = hashlib.sha256(json.dumps(operations, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if request_id is not None and (not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", request_id)):
            raise DomainError("Invalid request identifier")
        operations = self.normalize(operations)
        with transaction(self.engine) as connection:
            return self.stage(connection, operations, revision=revision, versions=versions,
                              request_id=request_id, notes_revision=notes_revision, digest=digest)

    def stage(self, connection, operations, *, revision=None, versions=None, request_id=None, notes_revision=None, digest=None):
        """Internal ticket staging on a managed connection; the caller owns commit."""
        if 'note_write_scope' not in connection.info or not connection.in_transaction():
            raise DomainError('Ticket staging requires a managed write transaction')
        if digest is None:
            digest = hashlib.sha256(json.dumps(operations, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with Session(connection, expire_on_commit=False) as session:
            if request_id:
                receipt = session.get(MutationReceipt, request_id)
                if receipt:
                    if receipt.digest != digest:
                        raise DomainError("Request identifier was already used", 409)
                    return json.loads(receipt.result_json)
            if notes_revision is not None and self.notes and self.notes.snapshot()["notes_revision"] != notes_revision:
                raise DomainError("Notes changed; request a new proposal", 409)
            before = snapshot(session)
            if revision is not None and revision != before["revision"]:
                raise DomainError("Graph changed; refresh the preview", 409, revision=before["revision"])
            for ticket_id, version in (versions or {}).items():
                ticket = session.get(Ticket, int(ticket_id))
                if ticket is None:
                    raise DomainError("Ticket was deleted", 409)
                if ticket.version != version:
                    raise DomainError("Ticket changed in another editor", 409, current=row_dict(ticket))
            after = reduce_graph(before, operations)
            changes = changes_between(before, after)
            meta = session.get(GraphMeta, 1)
            old = {t["id"]: t for t in before["tickets"]}
            new = {t["id"]: t for t in after["tickets"]}
            mapping = ticket_id_map(after['tickets'], meta.next_ticket_id)
            meta.next_ticket_id += len(mapping)
            for ticket_id, assigned_id in mapping.items():
                session.add(Ticket(id=assigned_id, title=new[ticket_id]['title']))
            session.flush()
            for ticket_id, data in new.items():
                row = session.get(Ticket, mapping.get(ticket_id, ticket_id))
                changed = ticket_id not in old or any(old[ticket_id][f] != data[f] for f in FIELDS)
                if not changed:
                    continue
                source_changed = ticket_id not in old or any(old[ticket_id][f] != data[f] for f in ("title", "description"))
                for field in FIELDS:
                    value = data[field]
                    setattr(row, field, mapping.get(value, value) if field == "parent_id" else value)
                if ticket_id in old:
                    row.version += 1
                if source_changed:
                    if ticket_id in old:
                        row.source_version += 1
                    row.translation_due, row.translation_error = time.time() + 3, False
                row.updated_at = dt.datetime.utcnow()
            session.flush()
            # Reassign children before deleting the old parent.
            for ticket_id in old.keys() - new.keys():
                session.delete(session.get(Ticket, ticket_id))
            session.flush()
            if before["edges"] != after["edges"]:
                session.execute(delete(TicketEdge))
                for edge in after["edges"]:
                    session.add(TicketEdge(**{k: mapping.get(v, v) for k, v in edge.items()}))
            remaining = {l["id"] for l in after["links"] if isinstance(l["id"], int)}
            for link in before["links"]:
                if link["id"] not in remaining:
                    session.execute(delete(CodeLink).where(CodeLink.id == link["id"]))
            for link in after["links"]:
                if isinstance(link["id"], str):
                    session.add(CodeLink(ticket_id=mapping.get(link["ticket_id"], link["ticket_id"]),
                                         **{k: link.get(k) for k in LINK_FIELDS}))
            if changes:
                meta.revision += 1
            session.flush()
            result = {"revision": meta.revision, "created": mapping,
                      "versions": {str(mapping.get(i, i)): session.get(Ticket, mapping.get(i, i)).version for i in new}}
            if request_id:
                session.add(MutationReceipt(request_id=request_id, digest=digest, result_json=json.dumps(result)))
            session.flush()
            return result

    def translate_one(self, translator):
        with transaction(self.engine) as connection:
            with Session(connection) as session:
                row = session.scalars(select(Ticket).where(Ticket.translation_due <= time.time()).order_by(Ticket.translation_due).limit(1)).first()
                if row is None:
                    return False
                ticket_id, version, title, description = row.id, row.source_version, row.title, row.description
        try:
            translated = translator(title, description)
            if not isinstance(translated, tuple) or len(translated) != 2 or not all(isinstance(s, str) for s in translated):
                raise ValueError("Invalid translation")
        except Exception:
            translated = None
        with transaction(self.engine) as connection:
            with Session(connection) as session:
                row = session.get(Ticket, ticket_id)
                if row is not None and row.source_version == version:
                    row.translation_due = None
                    row.translation_error = translated is None
                    if translated:
                        row.title_zh, row.description_zh = translated
                        row.translated_version = version
                    # A translation changes the graph, not the human draft version.
                    session.get(GraphMeta, 1).revision += 1
                    session.flush()
        return True

    def retry_translation(self, ticket_id):
        with transaction(self.engine) as connection:
            with Session(connection) as session:
                row = session.get(Ticket, ticket_id)
                if row is None:
                    raise DomainError("Ticket not found", 404)
                row.translation_due, row.translation_error = time.time() + 3, False
                session.get(GraphMeta, 1).revision += 1
                session.flush()
