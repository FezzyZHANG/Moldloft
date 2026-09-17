"""Human review renderings shared by the graph inspector and the tracked review file.

One line list feeds both surfaces, so the Markdown file a reviewer can git diff and the
inspector they click through show identical differences by construction. The record in
SQLite stays authoritative; the file is the human review form.
"""
from __future__ import annotations

import difflib
import json
from pathlib import Path

from .service import DomainError
from .writes import replace_bytes

TEXT_FIELDS = ('description', 'tech_notes', 'body')
TICKET_ORDER = ('title', 'status', 'priority', 'group_name', 'parent_id', 'description', 'tech_notes')
NOTE_ORDER = ('title', 'kind', 'state', 'abstract_zh', 'abstract_en', 'relations', 'body')


def line(kind, text):
    return {'kind': kind, 'text': text}


def scalar(value):
    if value is None or value == '':
        return '(empty)'
    return json.dumps(value, ensure_ascii=False) if isinstance(value, str) else str(value)


def ticket_label(ticket_id):
    return f'#{ticket_id}' if isinstance(ticket_id, int) else str(ticket_id)


def text_block(sign, field, value):
    kind = 'added' if sign == '+' else 'removed'
    return [line(kind, f'{sign} {field}:')] + [line(kind, f'{sign} {row}') for row in (value or '').splitlines()]


def text_diff(field, before, after):
    lines = [line('marker', f'@@ {field} @@')]
    diff = difflib.unified_diff((before or '').splitlines(), (after or '').splitlines(), lineterm='', n=2)
    for raw in list(diff)[2:]:
        if raw.startswith('@@'):
            lines.append(line('marker', raw))
        elif raw.startswith('+'):
            lines.append(line('added', '+ ' + raw[1:]))
        elif raw.startswith('-'):
            lines.append(line('removed', '- ' + raw[1:]))
        else:
            lines.append(line('context', '  ' + raw[1:]))
    return lines


def field_lines(order, before, after):
    """Authored-form differences: scalars as one line each, prose as a text diff, relations one per line."""
    lines = []
    for field in order:
        old, new = (before or {}).get(field), (after or {}).get(field)
        if before is not None and after is not None and old == new:
            continue
        if field == 'relations':
            olds = [f'{r["type"]} {r["target"]}' for r in (old or [])]
            news = [f'{r["type"]} {r["target"]}' for r in (new or [])]
            lines += [line('removed', f'- relation: {r}') for r in olds if r not in news]
            lines += [line('added', f'+ relation: {r}') for r in news if r not in olds]
        elif field in TEXT_FIELDS:
            if before is None:
                if new:
                    lines += text_block('+', field, new)
            elif after is None:
                if old:
                    lines += text_block('-', field, old)
            else:
                lines += text_diff(field, old, new)
        elif before is not None and after is not None:
            lines.append(line('removed', f'- {field}: {scalar(old)}'))
            lines.append(line('added', f'+ {field}: {scalar(new)}'))
        elif before is None and new not in (None, ''):
            lines.append(line('added', f'+ {field}: {scalar(new)}'))
        elif after is None and old not in (None, ''):
            lines.append(line('removed', f'- {field}: {scalar(old)}'))
    return lines


def describe(changes):
    """Turn reducer changes into review sections; the same output feeds page and file."""
    sections = []
    for change in changes:
        if change.get('ticket_id') is not None:
            sections.append({'target': f'ticket {ticket_label(change["ticket_id"])}', 'action': change['kind'],
                             'lines': field_lines(TICKET_ORDER, change['before'], change['after'])})
        elif change['kind'] in ('edges', 'links'):
            if change['kind'] == 'edges':
                signature = lambda e: f'{ticket_label(e["blocker_id"])} -> {ticket_label(e["blocked_id"])}'
            else:
                signature = lambda l: f'{ticket_label(l["ticket_id"])} {l["repo"]} {l["kind"]} {l["ref"]}' + (
                    f' {l["path"]}' if l.get('path') else '') + (
                    f':{l["start_line"]}-{l["end_line"] or l["start_line"]}' if l.get('start_line') else '')
            olds, news = [signature(v) for v in change['before']], [signature(v) for v in change['after']]
            lines = [line('removed', '- ' + v) for v in olds if v not in news] + [line('added', '+ ' + v) for v in news if v not in olds]
            sections.append({'target': 'dependencies' if change['kind'] == 'edges' else 'code links', 'action': 'changed', 'lines': lines})
        elif change['kind'] == 'note':
            sections.append({'target': f'note {change["note_id"]}', 'action': 'created' if change['before'] is None else 'updated',
                             'lines': field_lines(NOTE_ORDER, change['before'], change['after'])})
    return sections


def fence(lines):
    return ['```diff', *[l['text'] for l in lines], '```', '']


def render(proposal, sections):
    """Markdown for the tracked review file: record, rationale, differences, then the outcome."""
    captured = proposal.get('basis') or {}
    text = [f'# Proposal {proposal["id"]}', '',
            f'- status: {proposal["status"]}', f'- source: {proposal.get("source")}', f'- origin: {proposal.get("origin")}',
            f'- created: {proposal.get("created_at")}',
            f'- base revision: {captured.get("revision")}', f'- notes revision: {captured.get("notes_revision")}',
            f'- read basis: {len(captured.get("tickets") or {})} tickets, {len(captured.get("notes") or {})} notes, '
            + ('complete' if captured.get('complete') else 'incomplete'),
            '', '## Summary', '', proposal['summary'], '', '## Rationale', '', proposal['rationale'].rstrip('\n'), '', '## Changes', '']
    if sections is None:
        text += ['The differences can no longer be rendered against the current graph. Earlier versions of this file in Git hold the pending rendering.', '']
    for section in sections or []:
        text += [f'### {section["target"]} ({section["action"]})', '', *fence(section['lines'])]
    result = proposal.get('result') or {}
    if proposal['status'] != 'pending':
        text += ['## Outcome', '', f'- status: {proposal["status"]}']
        if result.get('decided_at'):
            text.append(f'- decided: {result["decided_at"]}')
        if proposal['status'] == 'applied':
            text.append(f'- revision: {proposal.get("applied_revision")}')
            if result.get('created'):
                text.append('- created: ' + ', '.join(f'{k} -> #{v}' for k, v in result['created'].items()))
            if result.get('notes'):
                text.append('- notes: ' + ', '.join(result['notes']))
        if proposal.get('stale_reason'):
            text.append(f'- reason: {proposal["stale_reason"]}')
        if result.get('reason'):
            text += ['', '### Reviewer reason', '', result['reason']]
        if result.get('note'):
            text += ['', '### Reviewer note', '', result['note']]
        if result.get('amendments'):
            text += ['', '### Reviewer amendments', '']
            for amendment in result['amendments']:
                names = tuple(amendment['fields'])
                lines = field_lines(names, {f: amendment['fields'][f]['proposed'] for f in names},
                                    {f: amendment['fields'][f]['applied'] for f in names})
                text += [f'#### {amendment["target"]}', '', *fence(lines)]
    return '\n'.join(text).rstrip('\n') + '\n'


class ReviewFiles:
    """One tracked Markdown file per proposal, regenerated from the record on each outcome."""

    def __init__(self, root):
        self.root = Path(root).resolve() if root else None

    def path(self, proposal):
        if self.root is None:
            return None
        return self.root / f'{str(proposal.get("created_at", ""))[:10]}-{proposal["id"]}.md'

    def write(self, proposal, sections):
        path = self.path(proposal)
        if path is None:
            return None
        try:
            replace_bytes(path, render(proposal, sections).encode('utf-8'))
        except OSError as error:
            raise DomainError('Review file could not be written; the proposal record is stored', 503) from error
        return path

    def remove(self, proposal):
        path = self.path(proposal)
        if path is not None and path.exists():
            path.unlink()
