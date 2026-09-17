"""Authored Markdown notes, independent of the ticket database.

Only note metadata and explicitly typed links are interpreted. Prose is never
executed, treated as a proof, or scanned for links to arbitrary local files.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from .service import DomainError
from .writes import transaction, register_replace, read_bytes, fault_point

KINDS = {
    "assumption": ("proposed", "adopted", "refuted", "retired"),
    "question": ("open", "resolved", "archived"),
    "idea": ("open", "selected", "parked"),
    "evidence": ("recorded", "superseded"),
    "decision": ("proposed", "accepted", "superseded"),
}
RELATIONS = {"assumes", "supports", "relates", "option"}
ABSTRACT_LIMITS = {"abstract_zh": 600, "abstract_en": 1200}
FIELDS = {"title", "kind", "state", "body", "relations", *ABSTRACT_LIMITS}
NOTE_ID = re.compile(r"n-[a-z0-9][a-z0-9-]{0,62}\Z")
TARGET = re.compile(r"(?:ticket:[1-9][0-9]*|note:n-[a-z0-9][a-z0-9-]{0,62})\Z")
MAX_BYTES = 128_000


def premise_digest(note):
    return hashlib.sha256(json.dumps([note["title"], note["body"]], ensure_ascii=False).encode("utf-8")).hexdigest()


def validate(note):
    if not isinstance(note.get("id"), str) or not NOTE_ID.fullmatch(note["id"]):
        raise DomainError("Invalid note ID")
    if not isinstance(note.get("title"), str) or not 1 <= len(note["title"].strip()) <= 200:
        raise DomainError("Note title is required (maximum 200 characters)")
    note["title"] = note["title"].strip()
    if not isinstance(note.get("kind"), str) or note["kind"] not in KINDS:
        raise DomainError("Invalid note kind")
    if note.get("state") not in KINDS[note["kind"]]:
        raise DomainError("State does not belong to this note kind")
    if not isinstance(note.get("body"), str) or len(note["body"]) > 20_000:
        raise DomainError("Note body exceeds 20000 characters")
    for field, limit in ABSTRACT_LIMITS.items():
        value = note.setdefault(field, "")
        if not isinstance(value, str) or len(value) > limit:
            raise DomainError(f"{field} must be text (maximum {limit} characters)")
        if len(value.splitlines()) > 1:
            raise DomainError(f"{field} must be one concise paragraph without line breaks")
        note[field] = value.strip()
    relations = note.get("relations")
    if not isinstance(relations, list) or len(relations) > 100:
        raise DomainError("Provide at most 100 note relations")
    seen = set()
    for relation in relations:
        if not isinstance(relation, dict) or set(relation) != {"type", "target"}:
            raise DomainError("A note relation needs type and target")
        kind, target = relation["type"], relation["target"]
        if not isinstance(kind, str) or kind not in RELATIONS or not isinstance(target, str) or not TARGET.fullmatch(target):
            raise DomainError("Invalid note relation")
        if target == f"note:{note['id']}" or (kind, target) in seen:
            raise DomainError("Self or duplicate note relation")
        if kind == "assumes" and note["kind"] != "assumption":
            raise DomainError("Only an assumption can supply an assumed premise")
        if kind == "supports" and note["kind"] != "evidence":
            raise DomainError("Only evidence can supply a supporting observation")
        if kind == "option" and note["kind"] != "idea":
            raise DomainError("Only an idea can be a candidate option")
        seen.add((kind, target))
    return note


def encode(note):
    header = {key: note[key] for key in ("id", "title", "kind", "state", "relations")}
    header.update({key: note.get(key, "") for key in ABSTRACT_LIMITS})
    if note.get("adopted_digest"):
        header["adopted_digest"] = note["adopted_digest"]
    # JSON scalars/flow arrays are valid YAML; no YAML runtime dependency is needed.
    text = "---\n" + "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in header.items())
    return (text + "\n---\n" + note["body"]).encode("utf-8")


def decode(raw, note_id):
    if len(raw) > MAX_BYTES:
        raise DomainError("Note exceeds the file size limit")
    try:
        text = raw.decode("utf-8-sig").replace("\r\n", "\n")
        if not text.startswith("---\n"):
            raise ValueError()
        header, body = text[4:].split("\n---\n", 1)
        note = {}
        for line in header.splitlines():
            key, value = line.split(":", 1)
            if key in note or key not in FIELDS | {"id", "adopted_digest"} or key == "body":
                raise ValueError()
            value = value.strip()
            try:
                note[key] = json.loads(value)
            except json.JSONDecodeError:
                if key not in {"id", "kind", "state", "title"} or not value or value[0] in "[{\"'":
                    raise ValueError()
                note[key] = value
        if note.get("id") != note_id:
            raise ValueError()
        note["body"] = body
        validate(note)
        digest = note.get("adopted_digest")
        if digest is not None and (not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)):
            raise ValueError()
        note["adoption_stale"] = note["kind"] == "assumption" and note["state"] == "adopted" and digest != premise_digest(note)
        if note["adoption_stale"]:
            # The file retains the old commitment; the current text needs fresh review.
            note["state"] = "proposed"
    except (UnicodeError, ValueError, TypeError, KeyError) as error:
        raise DomainError("Invalid note frontmatter; use the documented scalar/JSON-flow format") from error
    return {**note, "version": hashlib.sha256(raw).hexdigest(), "path": f"notes/{note_id}.md"}


class Notebook:
    def __init__(self, root, engine):
        self.root, self.engine = Path(root).resolve(), engine

    def path(self, note_id):
        if not isinstance(note_id, str) or not NOTE_ID.fullmatch(note_id):
            raise DomainError("Invalid note ID")
        path = self.root / f"{note_id}.md"
        if path.is_symlink() or path.resolve().parent != self.root:
            raise DomainError("Linked note paths are not supported")
        return path

    def read(self, note_id):
        try:
            path = self.path(note_id)
            with path.open("rb") as source:
                return decode(source.read(MAX_BYTES + 1), note_id)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise DomainError("Note file is unavailable", 503) from error

    def snapshot(self):
        notes, errors, signatures = [], [], []
        for path in sorted(self.root.glob("n-*.md")):
            if len(signatures) >= 500:
                errors.append({"file": "notes/", "message": "Only the first 500 note files are loaded"})
                break
            try:
                note = self.read(path.stem)
                if note:
                    notes.append(note)
                    signatures.append((path.name, note["version"]))
            except DomainError as error:
                errors.append({"file": path.name, "message": error.message})
                signatures.append((path.name, "unavailable"))
        revision = hashlib.sha256(json.dumps([signatures, errors], sort_keys=True).encode()).hexdigest()
        return {"notes": notes, "notes_revision": revision, "notes_errors": errors}

    def write(self, note_id, fields, version, ticket_ids):
        with transaction(self.engine, [self.path(note_id)]) as connection:
            return self.write_in_transaction(connection, note_id, fields, version, ticket_ids)

    def write_in_transaction(self, connection, note_id, fields, version, ticket_ids, *, planned_note_ids=()):
        """Validate and replace using the caller's managed write transaction."""
        if connection.engine is not self.engine:
            raise DomainError("Note connection belongs to a different engine")
        if not isinstance(fields, dict) or set(fields) - FIELDS:
            raise DomainError("Unknown note fields")
        path = self.path(note_id)
        prior_bytes = read_bytes(path)
        current = self.read(note_id)
        if current is None and version is not None:
            raise DomainError("Note was removed; keep your draft", 409, current={"deleted": True})
        candidate = {"id": note_id, "title": "", "kind": "question", "state": "open", "body": "", "relations": []}
        if current:
            candidate.update({key: current[key] for key in FIELDS})
        candidate.update(copy.deepcopy(fields))
        validate(candidate)
        if candidate["kind"] == "assumption" and candidate["state"] == "adopted":
            changed_premise = current and any(candidate[key] != current[key] for key in ("title", "body"))
            if changed_premise and fields.get("state") != "adopted":
                candidate["state"] = "proposed"
            else:
                candidate["adopted_digest"] = premise_digest(candidate)
        if current and current["version"] != version:
            # An uncertain response can be retried without overwriting a newer edit.
            if all(candidate[key] == current[key] for key in FIELDS):
                return current
            raise DomainError("Note changed on disk; compare before saving", 409, current=current)
        known = {f"ticket:{i}" for i in ticket_ids} | {f"note:{n['id']}" for n in self.snapshot()["notes"]}
        known |= {f'note:{i}' for i in planned_note_ids}
        previous = {(r["type"], r["target"]) for r in (current or {}).get("relations", [])}
        for relation in candidate["relations"]:
            if relation["target"] not in known and (relation["type"], relation["target"]) not in previous:
                raise DomainError("New relation target does not exist", 409)
        raw = encode(candidate)
        if current and all(candidate[key] == current[key] for key in FIELDS):
            return current
        temporary = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.root, prefix=".note-", suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
                output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            # Detect ordinary external-editor changes during preparation as well.
            latest = self.read(note_id)
            if (latest or {}).get("version") != (current or {}).get("version"):
                raise DomainError("Note changed on disk; keep your draft", 409, current=latest or {"deleted": True})
            register_replace(connection, path, prior_bytes, raw)
            os.replace(temporary, path)
            fault_point('note_replaced:' + note_id)
        except OSError as error:
            raise DomainError("Note could not be saved; keep your draft", 503) from error
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        return decode(raw, note_id)

    def decorate(self, graph, notebook=None):
        notebook = self.snapshot() if notebook is None else notebook
        graph = {**graph, **notebook}
        known = {f"ticket:{t['id']}" for t in graph["tickets"]} | {f"note:{n['id']}" for n in graph["notes"]}
        relations = [{**r, "source": f"note:{n['id']}", "owner": n["id"], "missing": r["target"] not in known}
                     for n in graph["notes"] for r in n["relations"]]
        graph["relations"] = relations
        adjacency = {key: [] for key in known}
        for edge in graph["edges"]:
            adjacency[f"ticket:{edge['blocker_id']}"].append(f"ticket:{edge['blocked_id']}")
        for relation in relations:
            if relation["type"] == "assumes" and not relation["missing"]:
                adjacency[relation["source"]].append(relation["target"])
        premises = {key: [] for key in known}
        for note in graph["notes"]:
            if note["kind"] != "assumption":
                continue
            root = f"note:{note['id']}"
            queue, seen = list(adjacency[root]), {root}
            while queue:
                key = queue.pop()
                if key in seen:
                    continue
                seen.add(key)
                premises[key].append(note["id"])
                queue.extend(adjacency[key])
        states = {n["id"]: n["state"] for n in graph["notes"]}
        for ticket in graph["tickets"]:
            ticket["assumptions"] = sorted(premises[f"ticket:{ticket['id']}"])
            ticket["review_notes"] = [i for i in ticket["assumptions"] if states[i] != "adopted"]
        return graph
