"""Deterministic graph operations. Ticket IDs stay symbolic in a preview."""

from collections import defaultdict
from copy import deepcopy
from difflib import unified_diff
from hashlib import sha256
import json
import re

from .errors import DomainError
from .git_truth import anchor_spec

TICKET_STATUSES = ("open", "in_progress", "done", "closed")
TICKET_PRIORITIES = ("low", "medium", "high")
NOTE_KINDS = {"assumption": ("proposed", "adopted", "refuted", "retired"),
              "question": ("open", "resolved"), "evidence": ("recorded", "superseded"),
              "decision": ("proposed", "accepted", "superseded"), "note": ("active", "archived")}
NOTE_RELATIONS = ("assumes", "supports", "challenges", "relates")
NOTE_ID_RE = re.compile(r"n-[a-z0-9][a-z0-9-]{0,62}\Z")
TEMP_ID_RE = re.compile(r"new:[A-Za-z0-9_-]{1,40}\Z")

TICKET_DEFAULTS = dict(title="", description="", tech_notes="", status="open", priority="medium",
                       group_name="", parent_id=None)
NOTE_DEFAULTS = dict(title="", kind="note", state="active", summary="", body="", in_graph=True)
ANCHOR_FIELDS = ("repo", "kind", "ref", "path", "start_line", "end_line", "symbol", "claim")
EDGE_KEY = ("blocker_id", "blocked_id")
LINK_KEY = ("source_note_id", "relation", "target_type", "target_id")
_OP_KEYS = {
    "ticket_create": ("temp_id", "fields"), "ticket_update": ("ticket_id", "fields"), "ticket_delete": ("ticket_id",),
    "edge_add": EDGE_KEY, "edge_remove": EDGE_KEY,
    "note_create": ("id", "fields"), "note_update": ("note_id", "fields"), "note_delete": ("note_id",),
    "anchor_add": ("target_type", "target_id", "anchor"), "anchor_update": ("anchor_id", "anchor"),
    "anchor_remove": ("anchor_id",),
}


def body_hash(title: str, body: str) -> str:
    return sha256(json.dumps([title, body], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _order(key):
    return (1, key) if isinstance(key, str) else (0, key)


def _id(value, kind):
    if kind == "ticket" and isinstance(value, str) and TEMP_ID_RE.fullmatch(value):
        return value
    if kind in ("ticket", "anchor"):
        if isinstance(value, str) and re.fullmatch("[0-9]{1,19}", value):
            value = int(value)
        if type(value) is int and 0 < value <= 2**63 - 1:
            return value
    elif kind == "note" and isinstance(value, str) and NOTE_ID_RE.fullmatch(value):
        return value
    raise DomainError(f"Invalid {kind} ID")


def _fields(value, allowed):
    if not isinstance(value, dict) or value.keys() - set(allowed):
        raise DomainError("Invalid or unknown fields")
    return value.copy()


def _choice(value, choices, name):
    if not isinstance(value, str) or value not in choices:
        raise DomainError(f"Invalid {name}")
    return value


def _validate_text(fields):
    for key, limit in (("title", 200), ("group_name", 80), ("description", 20000),
                       ("tech_notes", 20000), ("summary", 1000), ("body", 20000)):
        if key in fields:
            value = fields[key]
            if not isinstance(value, str) or len(value) > limit or (key == "title" and not value.strip()):
                raise DomainError(f"Invalid {key} (maximum {limit} characters)")


def _topological(items, children, name):
    """Iterative DFS retains the active path for a precise cycle report."""
    done, order = set(), []
    for root in items:
        if root in done:
            continue
        path = {root: 0}
        stack = [(root, iter(children[root]))]
        while stack:
            node, neighbors = stack[-1]
            child = next(neighbors, None)
            if child is None:
                stack.pop()
                del path[node]
                order.append(node)
                done.add(node)
            elif child in path:
                cycle = list(path)[path[child]:] + [child]
                raise DomainError(f"{name} cycle", 409, path=cycle)
            elif child not in done:
                path[child] = len(stack)
                stack.append((child, iter(children[child])))
    return reversed(order)


def enrich(graph: dict) -> dict:
    graph = deepcopy(graph)
    tickets = {row["id"]: row for row in graph["tickets"]}
    notes = {row["id"]: row for row in graph["notes"]}
    outgoing, blockers, premises = defaultdict(list), defaultdict(list), defaultdict(set)
    for edge in graph["edges"]:
        a, b = (edge[k] for k in EDGE_KEY)
        outgoing["ticket", a].append(("ticket", b))
        if tickets[a]["status"] != "done":
            blockers[b].append(a)
    for link in graph["note_links"]:
        if link["relation"] == "assumes":
            outgoing["note", link["source_note_id"]].append((link["target_type"], link["target_id"]))
    for key, note in notes.items():
        note["adoption_stale"] = (note["kind"] == "assumption" and note["state"] == "adopted"
                                  and note.get("adopted_body_hash") != body_hash(note["title"], note["body"]))
        if note["kind"] != "assumption" or not note["in_graph"]:
            continue
        pending, seen = [("note", key)], set()
        while pending:
            item = pending.pop()
            if item in seen:
                continue
            seen.add(item)
            kind, item_id = item
            if kind == "note" and not notes[item_id]["in_graph"]:
                continue
            if kind == "ticket":
                premises[item_id].add(key)
            pending.extend(outgoing[item])
    for key, ticket in tickets.items():
        ticket["blockers"] = sorted(blockers[key], key=_order)
        ticket["ready"] = ticket["status"] == "open" and not blockers[key]
        ticket["assumptions"] = sorted(premises[key])
        ticket["review_notes"] = [n for n in ticket["assumptions"]
                                  if notes[n]["state"] != "adopted" or notes[n]["adoption_stale"]]
    return graph


def graph_changes(before, after):
    changes = []
    for table, keys, fields in (("tickets", ("id",), TICKET_DEFAULTS),
                               ("edges", EDGE_KEY, EDGE_KEY),
                               ("notes", ("id",), (*NOTE_DEFAULTS, "adopted_body_hash")),
                               ("note_links", LINK_KEY, LINK_KEY),
                               ("anchors", ("id",), ("target_type", "target_id", *ANCHOR_FIELDS, "pinned_sha"))):
        def index(graph):
            return {tuple(row[k] for k in keys): {k: row.get(k) for k in fields} for row in graph[table]}
        old, new = index(before), index(after)
        for key in sorted(old.keys() | new.keys(), key=lambda ks: tuple(map(_order, ks))):
            a, b = old.get(key, {}), new.get(key, {})
            if a == b:
                continue
            delta = {}
            for field in fields:
                left, right = a.get(field), b.get(field)
                if left == right:
                    continue
                delta[field] = {"before": left, "after": right}
                if any(isinstance(v, str) and "\n" in v for v in (left, right)):
                    diff = unified_diff((left or "").splitlines(True), (right or "").splitlines(True), "before", "after")
                    delta[field]["diff"] = "".join(line if line.endswith("\n") else line + "\n" for line in diff)
            action = "create" if not a else "delete" if not b else "update"
            changes.append(dict(entity=table, id=key[0] if len(key) == 1 else list(key), action=action, fields=delta))
    return changes


class _Graph:
    def __init__(self, graph):
        self.tickets = {r["id"]: r.copy() for r in graph["tickets"]}
        self.notes = {r["id"]: r.copy() for r in graph["notes"]}
        self.anchors = {r["id"]: r.copy() for r in graph["anchors"]}
        self.edges = {tuple(r[k] for k in EDGE_KEY) for r in graph["edges"]}
        self.links = {tuple(r[k] for k in LINK_KEY) for r in graph["note_links"]}

    def target(self, kind, key):
        items = self.tickets if kind == "ticket" else self.notes
        if key not in items:
            raise DomainError(f"Unknown {kind} {key}", 404)
        return items[key]

    def ticket(self, op, creating):
        if creating:
            key = op.get("temp_id")
            if not isinstance(key, str) or not TEMP_ID_RE.fullmatch(key) or key in self.tickets:
                raise DomainError("Invalid or duplicate ticket temp_id")
            row = self.tickets[key] = dict(TICKET_DEFAULTS, id=key)
        else:
            key = op["ticket_id"] = _id(op.get("ticket_id"), "ticket")
            row = self.target("ticket", key)
        fields = op["fields"] = _fields(op.get("fields"), TICKET_DEFAULTS)
        if fields.get("parent_id") is not None:
            fields["parent_id"] = _id(fields["parent_id"], "ticket")
        row.update(fields)
        _validate_text(row)
        _choice(row["status"], TICKET_STATUSES, "status")
        _choice(row["priority"], TICKET_PRIORITIES, "priority")

    def note(self, op, creating):
        name = "id" if creating else "note_id"
        key = op[name] = _id(op.get(name), "note")
        if creating:
            if key in self.notes:
                raise DomainError(f"Duplicate note {key}", 409)
            row = self.notes[key] = dict(NOTE_DEFAULTS, id=key, adopted_body_hash=None)
        else:
            row = self.target("note", key)
        fields = op["fields"] = _fields(op.get("fields"), (*NOTE_DEFAULTS, "links"))
        adopted = row["kind"] == "assumption" and row["state"] == "adopted"
        changed_text = any(k in fields and fields[k] != row[k] for k in ("title", "body"))
        kind = _choice(fields.get("kind", row["kind"]), NOTE_KINDS, "note kind")
        if kind != row["kind"] or creating:
            row["state"] = NOTE_KINDS[kind][0]
        row.update({k: v for k, v in fields.items() if k != "links"})
        _validate_text(row)
        _choice(row["state"], NOTE_KINDS[kind], "note state")
        if type(row["in_graph"]) is not bool:
            raise DomainError("in_graph must be a boolean")
        if kind != "assumption" or row["state"] != "adopted":
            row["adopted_body_hash"] = None
        elif not adopted or ("state" in fields and not changed_text):
            row["adopted_body_hash"] = body_hash(row["title"], row["body"])
        if "links" in fields:
            if not isinstance(fields["links"], list):
                raise DomainError("links must be a list")
            self.links = {link for link in self.links if link[0] != key}
            for link in fields["links"]:
                _fields(link, LINK_KEY[1:])
                relation = _choice(link.get("relation"), NOTE_RELATIONS, "note relation")
                target = _choice(link.get("target_type"), ("ticket", "note"), "target type")
                target_id = link["target_id"] = _id(link.get("target_id"), target)
                item = (key, relation, target, target_id)
                if item in self.links or (target == "note" and key == target_id):
                    raise DomainError("Duplicate or self note link")
                self.links.add(item)

    def delete(self, kind, op):
        name = kind + "_id"
        key = op[name] = _id(op.get(name), kind)
        row = self.target(kind, key)
        if kind == "ticket":
            del self.tickets[key]
            for child in self.tickets.values():
                if child["parent_id"] == key:
                    child["parent_id"] = row["parent_id"]
            self.edges = {e for e in self.edges if key not in e}
        else:
            del self.notes[key]
        self.links = {l for l in self.links if l[2:] != (kind, key) and not (kind == "note" and l[0] == key)}
        self.anchors = {k: a for k, a in self.anchors.items() if (a["target_type"], a["target_id"]) != (kind, key)}

    def anchor(self, op, index):
        action = op["op"]
        if action == "anchor_add":
            key = f"new-anchor:{index}"
            target = _choice(op.get("target_type"), ("ticket", "note"), "anchor target")
            target_id = op["target_id"] = _id(op.get("target_id"), target)
            row = self.anchors[key] = dict(id=key, target_type=target, target_id=target_id)
        else:
            key = op["anchor_id"] = _id(op.get("anchor_id"), "anchor")
            if key not in self.anchors:
                raise DomainError(f"Unknown anchor {key}", 404)
            if action == "anchor_remove":
                del self.anchors[key]
                return
            row = self.anchors[key]
            for field in ("pinned_sha", "content_hash", "excerpt", "verified_at"):
                row.pop(field, None)
        fields = _fields(op.get("anchor"), ANCHOR_FIELDS)
        row.update(anchor_spec(row | fields))

    def finish(self):
        parents, dependencies = defaultdict(list), defaultdict(list)
        for key, row in self.tickets.items():
            if (parent := row["parent_id"]) is not None:
                self.target("ticket", parent)
                parents[parent].append(key)
        for a, b in sorted(self.edges, key=lambda e: tuple(map(_order, e))):
            self.target("ticket", a)
            self.target("ticket", b)
            dependencies[a].append(b)
        _topological(self.tickets, dependencies, "Dependency")
        for key in _topological(self.tickets, parents, "Containment"):
            for child in parents[key]:
                self.tickets[child]["group_name"] = self.tickets[key]["group_name"]
        for source, relation, target, key in self.links:
            self.target(target, key)
            required = {"assumes": "assumption", "supports": "evidence", "challenges": "evidence"}.get(relation)
            if required and self.notes[source]["kind"] != required:
                raise DomainError(f"{relation} requires a {required} note")
        for row in self.anchors.values():
            self.target(row["target_type"], row["target_id"])
        def rows(items, keys):
            ordered = sorted(items, key=lambda item: tuple(map(_order, item)))
            return [dict(zip(keys, item)) for item in ordered]
        return dict(tickets=list(self.tickets.values()), notes=list(self.notes.values()),
                    edges=rows(self.edges, EDGE_KEY), note_links=rows(self.links, LINK_KEY),
                    anchors=list(self.anchors.values()))


def reduce_graph(before: dict, operations: list[dict]) -> dict:
    if not isinstance(operations, list) or not 1 <= len(operations) <= 100:
        raise DomainError("Expected 1 to 100 operations")
    graph = _Graph(before)
    normalized = deepcopy(operations)
    for i, op in enumerate(normalized):
        action = op.get("op") if isinstance(op, dict) else None
        if not isinstance(action, str) or action not in _OP_KEYS:
            raise DomainError("Unknown graph operation")
        if op.keys() != {"op", *_OP_KEYS[action]}:
            raise DomainError(f"Unexpected or missing fields for {action}")
        if action in ("ticket_create", "ticket_update"):
            graph.ticket(op, action == "ticket_create")
        elif action in ("note_create", "note_update"):
            graph.note(op, action == "note_create")
        elif action in ("ticket_delete", "note_delete"):
            graph.delete(action.split("_")[0], op)
        elif action in ("edge_add", "edge_remove"):
            edge = tuple(_id(op.get(k), "ticket") for k in EDGE_KEY)
            op.update(zip(EDGE_KEY, edge))
            if action == "edge_add":
                graph.edges.add(edge)
            else:
                if edge not in graph.edges:
                    raise DomainError("Unknown dependency", 404)
                graph.edges.remove(edge)
        else:
            graph.anchor(op, i)
    after = before | graph.finish()
    return dict(preview=enrich(after), operations=normalized, changes=graph_changes(before, after))
