"""Captured read dependencies and conservative structural guards for proposals."""
from __future__ import annotations

import hashlib
import json

from .service import DomainError


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def structure(graph):
    return {
        'ticket_structure': digest({'tickets': sorted([(t['id'], t['parent_id'], t['group_name']) for t in graph['tickets']], key=str),
            'edges': sorted(graph['edges'], key=lambda e: (e['blocker_id'], e['blocked_id'])),
            'links': sorted(graph['links'], key=lambda link: link['id'])}),
        'note_structure': digest({'notes': sorted([(n['id'], n['kind'], sorted(n['relations'], key=lambda r: (r['type'], r['target'])))
                                                   for n in graph.get('notes', [])]), 'errors': graph.get('notes_errors', [])})}


def dependencies(graph, ticket_ids=(), note_ids=()):
    tickets = {t['id']: t for t in graph['tickets']}
    notes = {n['id']: n for n in graph.get('notes', [])}
    selected_tickets, selected_notes = set(ticket_ids), set(note_ids)
    if selected_tickets - tickets.keys() or selected_notes - notes.keys():
        raise DomainError('Read dependency no longer exists', 409)
    while True:
        old = (len(selected_tickets), len(selected_notes))
        for tid in list(selected_tickets):
            parent = tickets[tid]['parent_id']
            if parent is not None:
                selected_tickets.add(parent)
        for edge in graph['edges']:
            if edge['blocked_id'] in selected_tickets:
                selected_tickets.add(edge['blocker_id'])
        targets = {f'ticket:{t}' for t in selected_tickets} | {f'note:{n}' for n in selected_notes}
        for note in notes.values():
            if any(r['target'] in targets for r in note['relations']):
                selected_notes.add(note['id'])
            if note['id'] in selected_notes:
                for relation in note['relations']:
                    target = relation['target']
                    if target.startswith('ticket:') and int(target[7:]) in tickets:
                        selected_tickets.add(int(target[7:]))
                    if target.startswith('note:') and target[5:] in notes:
                        selected_notes.add(target[5:])
        if old == (len(selected_tickets), len(selected_notes)):
            break
    return selected_tickets, selected_notes


def capture(graph, ticket_ids=(), note_ids=(), complete=True):
    tids, nids = dependencies(graph, ticket_ids, note_ids)
    complete = bool(complete and not graph.get('notes_errors') and len(tids) <= 50 and len(nids) <= 20)
    tids, nids = set(sorted(tids)[:50]), set(sorted(nids)[:20])
    return {'revision': graph['revision'], 'notes_revision': graph.get('notes_revision'),
            'tickets': {str(t['id']): t['version'] for t in graph['tickets'] if t['id'] in tids},
            'notes': {n['id']: n['version'] for n in graph.get('notes', []) if n['id'] in nids},
            'complete': complete, **structure(graph)}


def validate_shape(basis):
    fields = {'revision', 'notes_revision', 'tickets', 'notes', 'complete', 'ticket_structure', 'note_structure'}
    if not isinstance(basis, dict) or set(basis) != fields:
        raise DomainError('Provide the complete read-result basis envelope')
    if type(basis['revision']) is not int or basis['revision'] < 0 or type(basis['complete']) is not bool:
        raise DomainError('Invalid basis revision/completeness')
    for name in ('notes_revision', 'ticket_structure', 'note_structure'):
        value = basis[name]
        if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise DomainError('Invalid basis fingerprint')
    if not isinstance(basis['tickets'], dict) or not isinstance(basis['notes'], dict) or len(basis['tickets']) > 50 or len(basis['notes']) > 20:
        raise DomainError('Invalid bounded dependency maps')
    for key, value in basis['tickets'].items():
        if not isinstance(key, str) or len(key) > 19 or not key.isascii() or not key.isdigit() or str(int(key)) != key or int(key) < 1 or type(value) is not int or value < 1:
            raise DomainError('Invalid ticket dependency version')
    from .notes import NOTE_ID
    for key, value in basis['notes'].items():
        if not isinstance(key, str) or not NOTE_ID.fullmatch(key) or not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise DomainError('Invalid note dependency hash')
    return basis


def targets(operations, graph):
    tids, nids = set(), set()
    owners = {l['id']: l['ticket_id'] for l in graph['links']}
    for op in operations:
        for field in ('ticket_id', 'blocker_id', 'blocked_id'):
            if type(op.get(field)) is int:
                tids.add(op[field])
        parent = op.get('fields', {}).get('parent_id')
        if type(parent) is int:
            tids.add(parent)
        if op.get('op') == 'link_remove' and op.get('link_id') in owners:
            tids.add(owners[op['link_id']])
        if op.get('op') == 'note_update' and isinstance(op.get('note_id'), str) and op['note_id'].startswith('n-'):
            nids.add(op['note_id'])
        for relation in op.get('fields', {}).get('relations', []):
            target = relation['target']
            if target.startswith('ticket:') and target[7:].isdigit():
                tids.add(int(target[7:]))
            elif target.startswith('note:n-'):
                # A note created in this batch is checked by the reducer instead.
                if any(n['id'] == target[5:] for n in graph.get('notes', [])):
                    nids.add(target[5:])
    return tids, nids


def check(basis, graph, operations):
    validate_shape(basis)
    if graph.get('notes_errors'):
        raise DomainError('Notebook is incomplete; proposal cannot be validated', 409)
    required_tickets, required_notes = targets(operations, graph)
    required_tickets |= {int(t) for t in basis['tickets']}
    required_notes |= set(basis['notes'])
    tids, nids = dependencies(graph, required_tickets, required_notes)
    scoped = basis['complete'] and tids <= {int(t) for t in basis['tickets']} and nids <= set(basis['notes'])
    structural_ops = any(op['op'] not in ('update', 'note_update') or
        (op['op'] == 'update' and {'parent_id', 'group_name'} & op.get('fields', {}).keys()) for op in operations)
    if not scoped or structural_ops:
        if basis['revision'] != graph['revision'] or basis['notes_revision'] != graph['notes_revision']:
            raise DomainError('Proposal expired: global basis changed or scoped dependencies are incomplete', 409)
    current_structure = structure(graph)
    for field in current_structure:
        if basis[field] != current_structure[field]:
            raise DomainError('Proposal expired: dependency or relation structure changed', 409)
    tickets = {str(t['id']): t['version'] for t in graph['tickets']}
    notes = {n['id']: n['version'] for n in graph['notes']}
    for key, version in basis['tickets'].items():
        if tickets.get(key) != version:
            raise DomainError(f'Proposal expired: ticket {key} read dependency changed', 409)
    for key, version in basis['notes'].items():
        if notes.get(key) != version:
            raise DomainError(f'Proposal expired: note {key} read dependency changed', 409)
    return 'scoped' if scoped and not structural_ops else 'global'
