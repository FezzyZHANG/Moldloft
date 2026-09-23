"""Pure note proposal reduction; persisted frontmatter never contains temporary IDs."""
from __future__ import annotations

import copy
import re

from .notes import FIELDS, NOTE_ID, validate, validate_targets, premise_digest
from .service import DomainError

TEMP = re.compile(r'new:[A-Za-z0-9_-]{1,40}\Z')
NOTE_OPS = {'note_create', 'note_update'}


def reduce_notes(before, after, operations):
    if before.get('notes_errors'):
        raise DomainError('Notebook data is incomplete; repair invalid notes before proposing changes', 409)
    notes = {n['id']: copy.deepcopy(n) for n in before.get('notes', [])}
    aliases = {}
    created = set()
    ticket_ids = {t['id'] for t in after['tickets']}
    ticket_aliases = {t['id'] for t in before['tickets'] if isinstance(t['id'], str)}
    ticket_aliases |= {op.get('temp_id', f'new:{i}') for i, op in enumerate(operations) if op.get('op') == 'create'}
    for op in operations:
        if op['op'] not in NOTE_OPS:
            continue
        allowed = {'op', 'temp_id', 'path', 'fields'} if op['op'] == 'note_create' else {'op', 'note_id', 'fields'}
        if set(op) - allowed:
            raise DomainError('Unknown note operation fields')
        if op['op'] == 'note_create':
            path, alias = op.get('path'), op.get('temp_id')
            match = re.fullmatch(r'notes/(n-[a-z0-9][a-z0-9-]{0,62})\.md', path) if isinstance(path, str) else None
            if not match or not isinstance(alias, str) or not TEMP.fullmatch(alias):
                raise DomainError('Note create requires notes/n-slug.md and a unique new:identifier')
            note_id = match[1]
            if note_id in notes or note_id in created:
                raise DomainError('Note create target already exists', 409)
            if alias in aliases or alias in ticket_aliases:
                raise DomainError('Temporary identifiers must be unique across tickets and notes')
            aliases[alias] = note_id
            created.add(note_id)
    if len(notes) + len(created) > 500:
        raise DomainError('Notebook would exceed the 500-note limit')
    known = {f'ticket:{i}' for i in ticket_ids} | {f'note:{i}' for i in notes.keys() | created}
    synthetic = {f'ticket:{i}': f'ticket:{j+1}' for j, i in enumerate(sorted(ticket_ids, key=str))}
    for op in operations:
        kind = op['op']
        if kind not in NOTE_OPS:
            continue
        note_id = aliases[op['temp_id']] if kind == 'note_create' else aliases.get(op.get('note_id'), op.get('note_id'))
        if not isinstance(note_id, str) or not NOTE_ID.fullmatch(note_id):
            raise DomainError('Invalid note ID')
        current = notes.get(note_id)
        if kind == 'note_update' and current is None:
            raise DomainError('Note update target does not exist', 404)
        fields = op.get('fields', {})
        if not isinstance(fields, dict) or set(fields) - FIELDS:
            raise DomainError('Unknown note fields')
        candidate = {'id': note_id, 'title': '', 'kind': 'question', 'state': 'open', 'body': '', 'relations': []}
        if current:
            candidate.update({key: current.get(key, '') for key in FIELDS})
        candidate.update(copy.deepcopy(fields))
        relations = candidate.get('relations')
        if not isinstance(relations, list):
            raise DomainError('Invalid note relations')
        for relation in relations:
            if isinstance(relation, dict) and isinstance(relation.get('target'), str):
                target = relation['target']
                if target.startswith('note:new:'):
                    alias = target[5:]
                    if alias not in aliases:
                        raise DomainError('Unknown temporary note target')
                    relation['target'] = f'note:{aliases[alias]}'
        original = copy.deepcopy(candidate['relations'])
        # Validate canonical syntax and kind rules using distinct legal placeholders.
        for relation in candidate['relations']:
            if isinstance(relation, dict) and isinstance(relation.get('target'), str) and relation['target'] in synthetic:
                relation['target'] = synthetic[relation['target']]
        validate(candidate)
        candidate['relations'] = original
        previous = {(r['type'], r['target']) for r in (current or {}).get('relations', [])}
        for relation in original:
            if relation['target'] not in known and (relation['type'], relation['target']) not in previous:
                raise DomainError('New relation target does not exist', 409)
        if candidate['kind'] == 'assumption' and candidate['state'] == 'adopted':
            changed = current and any(candidate[key] != current[key] for key in ('title', 'body'))
            if changed and fields.get('state') != 'adopted':
                candidate['state'] = 'proposed'
            else:
                candidate['adopted_digest'] = premise_digest(candidate)
        candidate['version'] = (current or {}).get('version')
        candidate['path'] = f'notes/{note_id}.md'
        candidate['adoption_stale'] = False
        notes[note_id] = candidate
    for note in notes.values():
        validate_targets(note, notes)
    return {**after, 'notes': sorted(notes.values(), key=lambda n: n['id']),
            'notes_revision': before.get('notes_revision'), 'notes_errors': [], 'note_ids': aliases}


def materialize(note, ticket_mapping):
    """Replace proposal-only ticket aliases after IDs are assigned in the SQL transaction."""
    note = copy.deepcopy(note)
    for relation in note['relations']:
        target = relation['target']
        if target.startswith('ticket:new:'):
            key = target[7:]
            if key not in ticket_mapping:
                raise DomainError('Unassigned temporary ticket target')
            relation['target'] = f'ticket:{ticket_mapping[key]}'
    validate(note)
    return note
