"""Preview/apply an authored grouping plan without rewriting ticket prose or status."""
from __future__ import annotations

import argparse
from contextlib import closing
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3

from .db import initialize
from .config import absolute_path, data_paths
from .service import Store, DomainError


def title_digest(graph, ids):
    titles = sorted((t['id'], t['title']) for t in graph['tickets'] if t['id'] in ids)
    return hashlib.sha256(json.dumps(titles, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()


def operations_for(graph, plan):
    by_id = {t['id']: t for t in graph['tickets']}
    assignments = [(i, group) for group, ids in plan['groups'].items() for i in ids]
    ids = {i for i, _ in assignments}
    if len(ids) != len(assignments) or not ids <= by_id.keys():
        raise DomainError('Plan has duplicate or missing ticket IDs')
    if title_digest(graph, ids) != plan['source_title_digest']:
        raise DomainError('Ticket titles changed; review this plan before applying it')
    operations = []
    for ticket_id, group in assignments:
        if by_id[ticket_id]['parent_id'] is not None:
            raise DomainError('Plan assumes root tickets; review the changed containment first')
        if by_id[ticket_id]['group_name'] != group:
            operations.append({'op': 'update', 'ticket_id': ticket_id, 'fields': {'group_name': group}})
    existing = {(e['blocker_id'], e['blocked_id']) for e in graph['edges']}
    for edge in plan['dependencies']:
        a, b = edge['blocker_id'], edge['blocked_id']
        if a not in ids or b not in ids or not edge.get('reason'):
            raise DomainError('Every dependency needs known endpoints and an authored reason')
        if (a, b) not in existing:
            operations.append({'op': 'edge_add', 'blocker_id': a, 'blocked_id': b})
            existing.add((a, b))
    return operations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--db', type=Path, default=None)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    path = absolute_path(args.plan, "--plan")
    plan = json.loads(path.read_text(encoding='utf-8'))
    database, _ = data_paths(args.db)
    if not database.is_file():
        raise DomainError('The target database must already exist')
    engine = initialize(database)
    try:
        store = Store(engine)
        before = store.graph()
        operations = operations_for(before, plan)
        preview = store.preview(operations, before) if operations else {'changes': []}
        print(json.dumps({'mode': 'apply' if args.apply else 'preview', 'groups': plan['groups'],
                          'dependencies': plan['dependencies'], 'changes': len(preview['changes'])}, indent=2))
        if not args.apply or not operations:
            return
        stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup = database.parent / 'backups' / f'tickets-before-organization-{stamp}.sqlite3'
        backup.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(backup)) as destination:
                source.backup(destination)
        result = store.mutate(operations, revision=before['revision'])
        print(json.dumps({'revision': result['revision'], 'backup': str(backup)}))
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
