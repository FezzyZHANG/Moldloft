"""Bounded pull-based MCP reads and proposal submission, never approval."""
from __future__ import annotations

import asyncio
import json
import os
import re

from . import basis as versions
from .proposals import Proposals, coherent_graph
from .notes import KINDS
from .service import DomainError

MAX_RESULT = 60000


def envelope(graph, items, *, ticket_ids=(), note_ids=(), complete=False, limit=50, **metadata):
    captured = versions.capture(graph, ticket_ids, note_ids, complete)
    output = []
    size = len(json.dumps(captured, ensure_ascii=False)) + len(json.dumps(metadata, ensure_ascii=False))
    for item in items[:limit]:
        encoded = json.dumps(item, ensure_ascii=False)
        if size + len(encoded) > MAX_RESULT:
            break
        output.append(item)
        size += len(encoded)
    truncated = len(output) < len(items) or any(k.endswith('_truncated') and v for k, v in metadata.items())
    if truncated:
        captured['complete'] = False
    return {'drydock_id': captured['drydock_id'], 'items': output, 'basis': captured, 'truncated': truncated,
            'complete': not truncated and not graph.get('notes_errors'), 'total_items': len(items), **metadata}


class ReadTools:
    def __init__(self, store):
        self.store = store

    def graph(self):
        return coherent_graph(self.store)

    def get_ready_work(self, limit=50):
        self.limit(limit)
        graph = self.graph()
        fields = ('id', 'title', 'status', 'priority', 'parent_id', 'group_name', 'blockers', 'ready')
        items = [{k: t[k] for k in fields} for t in graph['tickets'] if t['ready']]
        return envelope(graph, items, limit=limit)

    @staticmethod
    def limit(limit):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise DomainError('Limit must be an integer from 1 to 100')

    @staticmethod
    def ticket(graph, ticket_id):
        if type(ticket_id) is not int:
            raise DomainError('Ticket ID must be an integer')
        ticket = next((t for t in graph['tickets'] if t['id'] == ticket_id), None)
        if ticket is None:
            raise DomainError('Ticket not found', 404)
        return ticket

    def get_ticket(self, ticket_id):
        graph = self.graph()
        ticket = self.ticket(graph, ticket_id)
        tids, nids = versions.dependencies(graph, [ticket_id])
        items = [{'type': 'ticket', **ticket}]
        items += [{'type': 'note', **n} for n in graph['notes'] if n['id'] in nids]
        items += [{'type': 'ticket', **t} for t in graph['tickets'] if t['id'] in tids and t['id'] != ticket_id]
        return envelope(graph, items, ticket_ids=[ticket_id], complete=True, limit=70,
            children=[t['id'] for t in graph['tickets'] if t['parent_id'] == ticket_id][:100],
            children_truncated=sum(t['parent_id'] == ticket_id for t in graph['tickets']) > 100,
            subsequent=[e['blocked_id'] for e in graph['edges'] if e['blocker_id'] == ticket_id][:100],
            subsequent_truncated=sum(e['blocker_id'] == ticket_id for e in graph['edges']) > 100,
            code_links=[l for l in graph['links'] if l['ticket_id'] == ticket_id][:100],
            code_links_truncated=sum(l['ticket_id'] == ticket_id for l in graph['links']) > 100)

    def get_note(self, note_id):
        graph = self.graph()
        note = next((n for n in graph['notes'] if n['id'] == note_id), None)
        if note is None:
            raise DomainError('Note not found', 404)
        return envelope(graph, [{'type': 'note', **note}], note_ids=[note_id], complete=True)

    def search(self, query, kind=None, limit=50):
        self.limit(limit)
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise DomainError('Search query must contain 1-200 characters')
        if kind not in (None, 'ticket', *KINDS):
            raise DomainError('Invalid search kind')
        graph, items = self.graph(), []
        query = query.casefold().strip()
        if kind in (None, 'ticket'):
            for ticket in graph['tickets']:
                if query in ' '.join(str(ticket.get(f, '')) for f in ('id', 'title', 'description', 'tech_notes', 'title_zh', 'description_zh')).casefold():
                    items.append({'type': 'ticket', **{k: ticket[k] for k in ('id', 'title', 'status', 'priority', 'version')}})
        for note in graph['notes']:
            if kind in (None, note['kind']) and query in ' '.join(note.get(f, '') for f in ('id', 'title', 'abstract_en', 'abstract_zh')).casefold():
                items.append({'type': 'note', **{k: note[k] for k in ('id', 'title', 'kind', 'state', 'abstract_en', 'abstract_zh', 'version')}})
        return envelope(graph, items, limit=limit)

    def get_assumptions(self, ticket_id):
        graph = self.graph()
        ticket = self.ticket(graph, ticket_id)
        items = [n for n in graph['notes'] if n['id'] in ticket['assumptions']]
        return envelope(graph, items, ticket_ids=[ticket_id], complete=True, limit=20)

    def list_review_hints(self, limit=50):
        self.limit(limit)
        graph = self.graph()
        notes = {n['id']: n for n in graph['notes']}
        items = [{'ticket_id': t['id'], 'title': t['title'], 'notes': [
            {'id': n, 'state': notes[n]['state'], 'reason': 'adopted_text_changed' if notes[n]['adoption_stale'] else 'premise_not_adopted'}
            for n in t['review_notes']]} for t in graph['tickets'] if t['review_notes']]
        return envelope(graph, items, limit=limit)

    def resolve_ticket(self, ticket_id, limit=20):
        self.limit(limit)
        graph = self.graph()
        ticket = self.ticket(graph, ticket_id)
        target = f'ticket:{ticket_id}'
        linked = {n['id'] for n in graph['notes'] if any(r['target'] == target for r in n['relations'])}
        terms = {s for s in re.findall(r'\w+', (ticket['title'] + ' ' + ticket['description']).casefold()) if len(s) >= 3}
        terms -= {'the', 'and', 'that', 'this', 'with', 'from', 'for', 'ticket', 'notes'}
        items = []
        for note in graph['notes']:
            if note['id'] in ticket['assumptions']:
                direct = any(r['type'] == 'assumes' and r['target'] == target for r in note['relations'])
                items.append({'band': 'direct' if direct else 'inherited', 'linked': True, 'note': note})
        candidate_count = 0
        for note in graph['notes']:
            if note['kind'] == 'metadata':
                continue
            haystack = ' '.join(note.get(f, '') for f in ('title', 'abstract_en', 'abstract_zh')).casefold()
            if note['id'] not in linked and note['id'] not in ticket['assumptions'] and any(term in haystack for term in terms):
                legal = {'assumption': ['assumes', 'relates'], 'evidence': ['supports', 'relates'],
                         'idea': ['option', 'relates']}.get(note['kind'], ['relates'])
                candidate_count += 1
                items.append({'band': 'candidate', 'linked': False, 'legal_relations': legal,
                    'note': {k: note[k] for k in ('id', 'title', 'kind', 'state', 'abstract_en', 'abstract_zh', 'version')}})
        return envelope(graph, items, ticket_ids=[ticket_id], complete=False, limit=limit,
                        has_premise_mapping=bool(ticket['assumptions']), candidate_count=candidate_count,
                        candidate_method='lexical title/abstract overlap; unlinked suggestions are not premises')


def create_mcp(store_getter, drydock_lister):
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings
    allowed = [host.strip() for host in os.environ.get('TICKETS_MCP_ALLOWED_HOSTS', '').split(',') if host.strip()]
    security = None
    if allowed:
        hosts = ['127.0.0.1:*', 'localhost:*', '[::1]:*', *allowed]
        security = TransportSecuritySettings(allowed_hosts=hosts, allowed_origins=[f'http://{host}' for host in hosts])
    mcp = FastMCP('Moldloft', json_response=True, stateless_http=True, streamable_http_path='/mcp',
                 max_request_body_size=1_000_000, transport_security=security,
                 instructions='Call list_drydocks to choose a graph, then pass its stable drydock_id on every scoped call. '
                 'Omission always means default, never the browser selection. Read bounded ticket/note context '
                 'and its basis before proposing. Preserve uncertainty. '
                 'Submit ticket, note or mixed edits for human review in the graph page; no tool approves or applies. '
                 'A rejected outcome carries the reviewer reason; read it before proposing again. '
                 'English ticket fields are authoritative; cached translations are views, not proposal evidence. '
                 'Combine reads only within one drydock_id when revision and notes_revision match; '
                 'otherwise refresh one coherent basis. No tool creates, renames or switches graphs for the user.')

    async def read(method, drydock_id, *arguments):
        # Resolving a graph may open its DB and recover pending writes; keep that I/O
        # off the event loop and pin the target for the entire call.
        return await asyncio.to_thread(lambda: getattr(ReadTools(store_getter(drydock_id)), method)(*arguments))

    @mcp.tool()
    async def list_drydocks() -> dict:
        """Call first to discover stable graph IDs and names (at most 100). Select drydock_id explicitly on subsequent tools; this is a catalog, not a proposal basis."""
        return await asyncio.to_thread(drydock_lister)

    @mcp.tool()
    async def get_ready_work(limit: int = 50, drydock_id: str = 'default') -> dict:
        """Call to choose the next executable work. Returns open tickets without execution blockers only."""
        return await read('get_ready_work', drydock_id, limit)

    @mcp.tool()
    async def get_ticket(ticket_id: int, drydock_id: str = 'default') -> dict:
        """Call before modifying work; read its fields, dependency context and immutable proposal basis."""
        return await read('get_ticket', drydock_id, ticket_id)

    @mcp.tool()
    async def get_note(note_id: str, drydock_id: str = 'default') -> dict:
        """Call to read an authored note and hash. Metadata indexes require explicit reads; their navigation does not expand context."""
        return await read('get_note', drydock_id, note_id)

    @mcp.tool()
    async def search(query: str, kind: str | None = None, limit: int = 50, drydock_id: str = 'default') -> dict:
        """Call to find existing tickets or bilingual note abstracts, including kind='metadata' indexes. Search uses global guards."""
        return await read('search', drydock_id, query, kind, limit)

    @mcp.tool()
    async def get_assumptions(ticket_id: int, drydock_id: str = 'default') -> dict:
        """Call before building on a ticket to read direct/inherited premises and their states; support is not proof."""
        return await read('get_assumptions', drydock_id, ticket_id)

    @mcp.tool()
    async def list_review_hints(limit: int = 50, drydock_id: str = 'default') -> dict:
        """Call to review work affected by changed or unadopted premises. These hints never change execution readiness."""
        return await read('list_review_hints', drydock_id, limit)

    @mcp.tool()
    async def resolve_ticket(ticket_id: int, limit: int = 20, drydock_id: str = 'default') -> dict:
        """Call when premise mapping is missing/suspect. Returns factual mappings separately from unlinked lexical candidates; never writes or builds operations."""
        return await read('resolve_ticket', drydock_id, ticket_id, limit)

    @mcp.tool()
    async def get_proposal(proposal_id: str, rationale_offset: int = 0, rationale_limit: int = 8000,
                           drydock_id: str = 'default') -> dict:
        """Call to read an approval outcome or recover the stored reason. Page rationale explicitly; this read cannot be used as a new proposal basis."""
        if rationale_offset < 0 or not 1 <= rationale_limit <= 16000:
            raise DomainError('Invalid rationale page')
        proposal = await asyncio.to_thread(lambda: Proposals(store_getter(drydock_id)).get(proposal_id))
        text = proposal['rationale']
        result = {key: proposal[key] for key in ('id', 'source', 'summary', 'origin', 'status', 'created_at', 'applied_revision', 'result', 'stale_reason')}
        result.update(drydock_id=drydock_id, captured_basis=proposal['basis'], rationale=text[rationale_offset:rationale_offset+rationale_limit],
            rationale_offset=rationale_offset, rationale_total=len(text),
            truncated=rationale_offset+rationale_limit < len(text), basis=None)
        return result

    @mcp.tool()
    async def propose_changes(operations: list[dict], base_revision: int, notes_revision: str, basis: dict,
                        summary: str, rationale: str, origin: str, request_id: str, drydock_id: str = 'default') -> dict:
        """Call after coherent reads to queue ticket, note or mixed edits for human review. Preserve the read basis; retry the same request ID and content after uncertainty. Never applies."""
        if basis.get('revision') != base_revision or basis.get('notes_revision') != notes_revision:
            raise DomainError('Base revisions must match the captured read basis')
        proposal = await asyncio.to_thread(lambda: Proposals(store_getter(drydock_id)).submit(
            operations, basis, summary, rationale, origin, request_id))
        return {'drydock_id': drydock_id, 'proposal_id': proposal['id'], 'status': proposal['status']}

    return mcp
