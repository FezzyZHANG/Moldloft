"""Durable reviewed proposals: pure checks, recoverable apply, tracked review files.

No conversation or prompt is stored. Reads never change a record; only an explicit apply or
reject persists an outcome. SQL and Markdown use ordered writes with durable recovery.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import re
import uuid

from sqlalchemy import select, delete, func
from sqlalchemy.orm import Session

from . import basis
from .models import Proposal, ProposalReceipt, NoteWriteAttempt, GraphMeta
from .review import ReviewFiles, describe
from .service import DomainError, snapshot, row_dict, ticket_id_map, FIELDS as TICKET_FIELDS
from .writes import transaction, writer_gate

NOTE_OPS = {'note_create', 'note_update'}
FIELD_OPS = {'create', 'update', 'note_create', 'note_update'}


class Expired(DomainError):
    """A proposal failed revalidation during an explicit action."""


def graph_at(store, connection):
    with Session(connection) as session:
        graph = snapshot(session)
    graph['drydock_id'] = store.drydock_id
    return store.notes.decorate(graph) if store.notes else {**graph, 'notes': [], 'notes_revision': '', 'notes_errors': []}


def coherent_graph(store):
    with writer_gate(store.engine):
        with store.engine.connect() as connection:
            connection.exec_driver_sql('BEGIN')
            if connection.execute(select(NoteWriteAttempt.id).limit(1)).first():
                raise DomainError('Recovery is unresolved; a complete read basis is unavailable', 503)
            graph = graph_at(store, connection)
        if store.notes and store.notes.snapshot()['notes_revision'] != graph['notes_revision']:
            raise DomainError('Notebook changed during read; refresh the complete basis', 409)
        return graph


def public(row):
    data = row_dict(row)
    data['basis'] = json.loads(data.pop('basis_json'))
    data['operations'] = json.loads(data.pop('operations_json'))
    data['result'] = json.loads(data.pop('result_json')) if data.get('result_json') else None
    data.pop('result_json', None)
    data['truncated'] = False
    return data


def default_review_root(store, root=None):
    if root is not None:
        return root
    return store.notes.root.parent / 'review' if store.notes is not None else None


def now():
    return dt.datetime.utcnow().replace(microsecond=0).isoformat()


def reviewer_text(value, *, required, label):
    value = '' if value is None else value
    if not isinstance(value, str) or len(value) > 4000:
        raise DomainError(f'{label} must be a string of at most 4000 characters')
    value = value.strip()
    if required and not value:
        raise DomainError(f'{label} is required')
    return value


def operation_label(op):
    if op['op'] in NOTE_OPS:
        return f'note {op.get("note_id", op.get("temp_id"))}'
    target = op.get('ticket_id', op.get('temp_id'))
    return f'ticket #{target}' if isinstance(target, int) else f'ticket {target}'


def amend(operations, edits):
    """Apply reviewer edits to a copy of the proposed operations and describe the difference."""
    from .notes import FIELDS as NOTE_FIELDS
    operations, amendments = copy.deepcopy(operations), []
    for edit in edits or []:
        if not isinstance(edit, dict) or type(edit.get('index')) is not int or not isinstance(edit.get('fields'), dict):
            raise DomainError('Reviewer edits need an operation index and a fields object')
        index, fields = edit['index'], edit['fields']
        if not 0 <= index < len(operations) or operations[index].get('op') not in FIELD_OPS:
            raise DomainError('Reviewer edits apply to create and update operations only')
        op = operations[index]
        allowed = (NOTE_FIELDS if op['op'] in NOTE_OPS else TICKET_FIELDS) - {'relations', 'parent_id'}
        proposed = op.setdefault('fields', {})
        changed = {}
        for field, value in fields.items():
            if field not in allowed or field not in proposed:
                raise DomainError('Reviewer edits are limited to text fields the proposal itself sets')
            if not isinstance(value, str):
                raise DomainError('Reviewer edit values must be strings')
            if value != proposed[field]:
                changed[field] = {'proposed': proposed[field], 'applied': value}
                proposed[field] = value
        if changed:
            amendments.append({'index': index, 'target': operation_label(op), 'fields': changed})
    return operations, amendments


class Proposals:
    def __init__(self, store, review_root=None):
        self.store, self.engine = store, store.engine
        self.files = ReviewFiles(default_review_root(store, review_root))

    # -- submission -------------------------------------------------------------------

    def submit(self, operations, captured_basis, summary, rationale, origin, request_id, source='mcp'):
        basis.validate_shape(captured_basis)
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 500 or len(summary.splitlines()) != 1:
            raise DomainError('Summary must be one line of at most 500 characters')
        if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 32000:
            raise DomainError('Rationale must contain 1-32000 characters; oversized input is rejected, not silently truncated')
        if origin not in ('free', 'resolve') or source not in ('mcp', 'copilot'):
            raise DomainError('Invalid proposal origin or source')
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', request_id):
            raise DomainError('Invalid proposal request ID')
        # Hash the submitted content and immutable preconditions, before normalization.
        submitted = copy.deepcopy({'operations': operations, 'basis': captured_basis, 'summary': summary,
                                   'rationale': rationale, 'origin': origin, 'source': source})
        if len(json.dumps(submitted, ensure_ascii=False)) > 256000:
            raise DomainError('Proposal exceeds the 256000-character storage limit')
        request_hash = basis.digest(submitted)
        changes = None
        with transaction(self.engine) as connection:
            with Session(connection) as session:
                existing = session.scalar(select(Proposal).where(Proposal.request_id == request_id))
                if existing:
                    if existing.request_hash != request_hash:
                        raise DomainError('Request ID was already used with different content or preconditions', 409)
                    record = public(existing)
                else:
                    receipt = session.scalar(select(ProposalReceipt).where(ProposalReceipt.request_id == request_id))
                    if receipt:
                        if receipt.request_hash != request_hash:
                            raise DomainError('Request ID was already used with different content or preconditions', 409)
                        return json.loads(receipt.result_json)
                    graph = graph_at(self.store, connection)
                    preview = self.store.preview(operations, graph)
                    normalized = preview['operations']
                    basis.check(captured_basis, graph, normalized)
                    if origin == 'resolve' and any(op['op'] not in NOTE_OPS for op in normalized):
                        raise DomainError('Resolve-origin proposals can contain note operations only')
                    row = Proposal(id=str(uuid.uuid4()), request_id=request_id, request_hash=request_hash, source=source,
                        base_revision=captured_basis['revision'], notes_revision=captured_basis['notes_revision'],
                        basis_json=json.dumps(captured_basis, sort_keys=True), operations_json=json.dumps(normalized),
                        summary=summary.strip(), rationale=rationale, origin=origin, status='pending')
                    session.add(row)
                    session.flush()
                    record, changes = public(row), preview['changes']
        if changes is not None:
            self.render_file(record, changes)
        else:
            path = self.files.path(record)
            if path is not None and not path.exists():
                self.render_file(record)
        return record

    # -- reads that never write -------------------------------------------------------

    def get(self, proposal_id):
        with Session(self.engine) as session:
            row = session.get(Proposal, proposal_id)
            if not row:
                receipt = session.get(ProposalReceipt, proposal_id)
                if receipt:
                    return json.loads(receipt.result_json)
                raise DomainError('Proposal not found', 404)
            return public(row)

    def pending_count(self):
        with Session(self.engine) as session:
            return session.scalar(select(func.count(Proposal.id)).where(Proposal.status == 'pending'))

    def fingerprint(self):
        """Changes whenever a proposal arrives or its stored status changes; carried on the SSE event."""
        with Session(self.engine) as session:
            rows = session.execute(select(Proposal.id, Proposal.status).order_by(Proposal.id)).all()
        return basis.digest([list(row) for row in rows])[:16]

    def list(self, status='pending', limit=50, offset=0, origin=None, *, check=False):
        if status not in ('pending', 'applied', 'rejected', 'expired', 'all'):
            raise DomainError('Invalid proposal status filter')
        if type(limit) is not int or not 1 <= limit <= 100:
            raise DomainError('Proposal list limit must be 1-100')
        if type(offset) is not int or offset < 0 or origin not in (None, 'free', 'resolve'):
            raise DomainError('Invalid proposal offset or origin filter')
        with Session(self.engine) as session:
            query = select(Proposal).order_by(Proposal.created_at, Proposal.id)
            if status != 'all':
                query = query.where(Proposal.status == status)
            if origin:
                query = query.where(Proposal.origin == origin)
            rows = list(session.scalars(query.offset(offset).limit(limit+1)))
            # Queue summaries exclude potentially large bodies/rationale/basis maps.
            items = [{key: getattr(row, key).isoformat() if key == 'created_at' else getattr(row, key)
                      for key in ('id', 'source', 'summary', 'origin', 'status', 'created_at', 'stale_reason')}
                     for row in rows[:limit]]
            if check:
                graph = self.store.graph()
                for item, row in zip(items, rows[:limit]):
                    item.update(self.summarize(row, graph))
        return {'items': items, 'truncated': len(rows) > limit, 'next_offset': offset+limit if len(rows) > limit else None}

    def summarize(self, row, graph):
        """Targets, ticket groups and a live check for one queue entry; no row is changed."""
        operations = json.loads(row.operations_json)
        tickets = {t['id']: t for t in graph['tickets']}
        notes = {n['id']: n for n in graph.get('notes', [])}
        try:
            tids, nids = basis.targets(operations, graph)
        except DomainError:
            tids, nids = set(), set()
        for note_id in list(nids):
            for relation in notes.get(note_id, {}).get('relations', []):
                target = relation['target']
                if target.startswith('ticket:') and target[7:].isdigit():
                    tids.add(int(target[7:]))
        groups = sorted({tickets[t]['group_name'] for t in tids if t in tickets})
        # New-only proposals have no stored targets yet; use their derived root groups.
        if any(op['op'] == 'create' for op in operations):
            try:
                preview = self.store.preview(operations, graph)['preview']
                groups = sorted(set(groups) | {t['group_name'] for t in preview['tickets']
                                              if isinstance(t['id'], str)})
            except DomainError:
                pass
        stores = {'notes' if op['op'] in NOTE_OPS else 'tickets' for op in operations}
        check = None
        if row.status == 'pending':
            try:
                mode = basis.check(json.loads(row.basis_json), graph, operations)
                check = {'applicable': True, 'reason': None, 'mode': mode}
            except DomainError as error:
                check = {'applicable': False, 'reason': error.message, 'mode': None}
        return {'groups': groups, 'targets': {'tickets': sorted(tids), 'notes': sorted(nids)}, 'check': check,
                'operation_count': len(operations), 'store': 'mixed' if len(stores) > 1 else next(iter(stores))}

    def validate(self, proposal_id):
        """Read-only review: stored record, a live check against the current graph, and the rendered differences."""
        proposal = self.get(proposal_id)
        if proposal.get('cleared') or proposal['status'] != 'pending':
            return {'proposal': proposal, 'check': None, 'preview': None, 'sections': None}
        graph = self.store.graph()
        try:
            check = {'applicable': True, 'reason': None, 'mode': basis.check(proposal['basis'], graph, proposal['operations'])}
        except DomainError as error:
            check = {'applicable': False, 'reason': error.message, 'mode': None}
        # A stale proposal still shows what it proposed, so the reviewer can write a reason.
        try:
            preview = self.store.preview(proposal['operations'], graph)
            sections = describe(preview['changes'])
        except DomainError:
            preview, sections = None, None
        return {'proposal': proposal, 'check': check, 'preview': preview, 'sections': sections}

    # -- explicit actions ---------------------------------------------------------------

    def reject(self, proposal_id, reason):
        reason = reviewer_text(reason, required=True, label='A rejection reason')
        with transaction(self.engine) as connection:
            with Session(connection) as session:
                row = session.get(Proposal, proposal_id)
                if row is None:
                    receipt = session.get(ProposalReceipt, proposal_id)
                    if receipt:
                        return json.loads(receipt.result_json)
                    raise DomainError('Proposal not found', 404)
                if row.status == 'pending':
                    row.status, row.result_json = 'rejected', json.dumps({'decided_at': now(), 'reason': reason})
                    session.flush()
                elif row.status != 'rejected':
                    raise DomainError(f'Proposal is {row.status}; it cannot be rejected', 409)
                outcome = public(row)
        self.render_file(outcome)
        return outcome

    def apply(self, proposal_id, edits=(), note=''):
        """Human approval entry point; never registered as an MCP tool."""
        note = reviewer_text(note, required=False, label='A reviewer note')
        # Read status and predict IDs under the same cross-process guard as compensation.
        # A concurrent retry must see the committed outcome before checking the old basis.
        with writer_gate(self.engine):
            proposal = self.get(proposal_id)
            if proposal.get('cleared') or proposal['status'] == 'applied':
                outcome = proposal
            else:
                if proposal['status'] != 'pending':
                    raise DomainError(f'Proposal is {proposal["status"]}; a new proposal is required', 409)
                operations, amendments = amend(proposal['operations'], edits)
                try:
                    if any(op['op'] in NOTE_OPS for op in operations):
                        outcome = self.apply_with_notes(proposal, operations, amendments, note)
                    else:
                        outcome = self.apply_tickets(proposal, operations, amendments, note)
                except Expired as error:
                    outcome = self.expire(proposal_id, error.message)
                    self.render_file(outcome)
                    raise DomainError('Proposal expired: ' + error.message, 409, proposal=outcome) from error
        self.render_file(outcome)
        return outcome

    def apply_tickets(self, proposal, operations, amendments, note):
        with transaction(self.engine, guard_held=True) as connection:
            current = graph_at(self.store, connection)
            try:
                basis.check(proposal['basis'], current, operations)
                preview = self.store.preview(operations, current)
            except DomainError as error:
                raise Expired(error.message) from error
            result = self.store.stage(connection, preview['operations'], revision=current['revision'],
                                      request_id='proposal-' + proposal['id'])
            return self.finish(connection, proposal['id'], {**result, 'notes': []}, amendments, note)

    def apply_with_notes(self, proposal, operations, amendments, note):
        from .note_ops import materialize
        from .notes import encode, FIELDS as NOTE_FIELDS
        with self.engine.connect() as connection:
            connection.exec_driver_sql('BEGIN')
            if connection.execute(select(NoteWriteAttempt.id).limit(1)).first():
                raise DomainError('Unresolved note write recovery; ordinary writes are blocked', 503)
            graph = graph_at(self.store, connection)
            next_id = connection.execute(select(GraphMeta.next_ticket_id).where(GraphMeta.id == 1)).scalar_one()
        try:
            basis.check(proposal['basis'], graph, operations)
            preview = self.store.preview(operations, graph)
        except DomainError as error:
            raise Expired(error.message) from error
        changed = {c['note_id'] for c in preview['changes'] if c['kind'] == 'note'}
        mapping = ticket_id_map(preview['preview']['tickets'], next_id)
        notes = [materialize(n, mapping) for n in preview['preview']['notes'] if n['id'] in changed]
        planned = {self.store.notes.path(n['id']): encode(n) for n in notes}
        before = {n['id']: n for n in graph['notes']}
        ticket_ops = [op for op in preview['operations'] if op['op'] not in NOTE_OPS]
        # Persist both byte images and assigned IDs before SQL staging or file replacement.
        with transaction(self.engine, planned.keys(), planned=planned,
                         context={'proposal_id': proposal['id'], 'assigned_ids': mapping}, guard_held=True) as connection:
            current = graph_at(self.store, connection)
            try:
                basis.check(proposal['basis'], current, operations)
            except DomainError as error:
                raise Expired(error.message) from error
            if ticket_ops:
                result = self.store.stage(connection, ticket_ops, revision=current['revision'],
                                          request_id='proposal-' + proposal['id'])
                if result['created'] != mapping:
                    raise DomainError('Assigned ticket IDs changed during staging', 409)
            else:
                result = {'revision': current['revision'], 'created': {}, 'versions': {}}
            ids = {mapping.get(t['id'], t['id']) for t in preview['preview']['tickets']}
            planned_ids = {n['id'] for n in notes}
            for n in notes:
                self.store.notes.write_in_transaction(connection, n['id'], {k: n[k] for k in NOTE_FIELDS},
                    (before.get(n['id']) or {}).get('version'), ids, planned_note_ids=planned_ids)
            result['notes'] = sorted(changed)
            return self.finish(connection, proposal['id'], result, amendments, note)

    def finish(self, connection, proposal_id, result, amendments, note):
        result = {**result, 'decided_at': now(), 'note': note, 'amendments': amendments}
        with Session(connection) as session:
            row = session.get(Proposal, proposal_id)
            row.status, row.applied_revision, row.result_json = 'applied', result['revision'], json.dumps(result)
            session.flush()
            return public(row)

    def expire(self, proposal_id, reason):
        with transaction(self.engine, guard_held=True) as connection:
            with Session(connection) as session:
                row = session.get(Proposal, proposal_id)
                if row is not None and row.status == 'pending':
                    row.status, row.stale_reason = 'expired', reason
                    row.result_json = json.dumps({'decided_at': now(), 'attempted': 'apply'})
                    session.flush()
                return public(row)

    def clear(self, statuses, *, apply=False):
        if not statuses or set(statuses) - {'applied', 'rejected', 'expired'}:
            raise DomainError('Cleanup requires explicit non-pending statuses')
        with transaction(self.engine) as connection:
            with Session(connection) as session:
                rows = session.execute(select(Proposal.id, Proposal.request_id, Proposal.request_hash,
                    Proposal.source, Proposal.status, Proposal.created_at, Proposal.applied_revision, Proposal.result_json,
                    Proposal.stale_reason).where(Proposal.status.in_(statuses)).order_by(Proposal.id)).all()
                if apply:
                    for row in rows:
                        result = {'id': row.id, 'source': row.source, 'status': row.status, 'cleared': True,
                            'summary': 'Manually cleared proposal', 'rationale': '', 'origin': None,
                            'basis': None, 'operations': [], 'created_at': row.created_at.isoformat(),
                            'applied_revision': row.applied_revision, 'result': json.loads(row.result_json) if row.result_json else None,
                            'stale_reason': row.stale_reason, 'truncated': False}
                        session.add(ProposalReceipt(id=row.id, request_id=row.request_id, request_hash=row.request_hash,
                                                   result_json=json.dumps(result)))
                        session.execute(delete(Proposal).where(Proposal.id == row.id))
                        self.files.remove(result)
                    session.flush()
                return {'count': len(rows), 'applied': apply, 'statuses': sorted(set(statuses)),
                        'pending_preserved': True, 'replay_receipts_retained': True}

    # -- tracked review file ------------------------------------------------------------

    def render_file(self, proposal, changes=None):
        """Best-effort tracked rendering of a stored record; SQLite remains authoritative."""
        if self.files.root is None or proposal.get('cleared'):
            return None
        if changes is None:
            try:
                changes = self.store.preview(proposal['operations'], self.store.graph())['changes']
            except DomainError:
                changes = None
        return self.files.write(proposal, describe(changes) if changes is not None else None)
