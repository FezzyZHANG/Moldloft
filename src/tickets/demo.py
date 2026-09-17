"""Disposable DAG demo. Run from the main root: python -m tickets.demo."""
from contextlib import asynccontextmanager
from pathlib import Path
import argparse
import tempfile

from .app import create_app


def create_demo_app(path, *, copilot_streamer=None):
    app = create_app(path, translator=None, copilot_streamer=copilot_streamer, demo=True)
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(instance):
        async with original_lifespan(instance):
            if not instance.state.store.graph()['tickets']:
                tasks = [
                    ('Delivery workflow', 'Platform', None, 'in_progress', 'high'),
                    ('Define graph schema', '', 'new:0', 'done', 'high'),
                    ('Build graph API', '', 'new:0', 'in_progress', 'high'),
                    ('Validate concurrent edits', '', 'new:2', 'open', 'medium'),
                    ('Review release checklist', '', 'new:0', 'open', 'medium'),
                    ('Editor experience', 'Interface', None, 'in_progress', 'high'),
                    ('Save on attention change', '', 'new:5', 'open', 'high'),
                    ('Preview Copilot proposals', '', 'new:5', 'open', 'medium'),
                    ('Keyboard navigation', '', 'new:5', 'open', 'low'),
                ]
                operations = [{'op': 'create', 'temp_id': f'new:{i}', 'fields': {
                    'title': title, 'group_name': group, 'parent_id': parent, 'status': status,
                    'priority': priority, 'description': 'Synthetic demonstration ticket. Safe to edit or delete.',
                    'tech_notes': 'Use isolated fixtures. Preserve drafts until the server confirms the save.'}}
                    for i, (title, group, parent, status, priority) in enumerate(tasks)]
                operations += [{'op': 'edge_add', 'blocker_id': f'new:{a}', 'blocked_id': f'new:{b}'}
                               for a, b in [(1, 2), (2, 3), (3, 4), (2, 6), (6, 7), (7, 8)]]
                instance.state.store.mutate(operations)
                notebook = instance.state.notes
                fixtures = [
                    ('n-single-operator', 'One operator with several browser tabs', 'assumption', 'adopted',
                     'Assume one operator edits through several tabs. Revisit this premise if independent people need concurrent text editing.',
                     [{'type': 'assumes', 'target': 'ticket:3'}]),
                    ('n-save-question', 'How should concurrent saves be resolved?', 'question', 'open',
                     'Compare the cost of explicit conflict review with automatic merging. A choice is still needed.',
                     [{'type': 'relates', 'target': 'ticket:4'}]),
                    ('n-review-option', 'Keep explicit conflict review', 'idea', 'open',
                     'Show both versions and let the operator retain their edits. Candidate only; not an additional required task.',
                     [{'type': 'option', 'target': 'note:n-save-question'}]),
                    ('n-merge-option', 'Explore automatic text merging', 'idea', 'parked',
                     'Try a small merge experiment only if conflict review repeatedly interrupts actual work.',
                     [{'type': 'option', 'target': 'note:n-save-question'}]),
                    ('n-fixture-evidence', 'Two-tab conflict fixture', 'evidence', 'recorded',
                     'Synthetic example of an evidence note. It records where a check belongs, not a claim about a real user session.',
                     [{'type': 'supports', 'target': 'note:n-single-operator'}, {'type': 'relates', 'target': 'ticket:4'}]),
                ]
                for note_id, title, kind, state, body, relations in fixtures:
                    notebook.write(note_id, dict(title=title, kind=kind, state=state, body=body, relations=relations), None, set(range(1, 10)))
            yield

    app.router.lifespan_context = lifespan
    return app


if __name__ == '__main__':
    from .server import run
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8791)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='moldloft-demo-') as tmp:
        run(create_demo_app(Path(tmp) / 'demo.db'), port=args.port)
