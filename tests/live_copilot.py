"""Opt-in GLM integration check. Sends synthetic tickets and consumes model usage.

Run manually from the repository root: python -B -m tests.live_copilot
Never run as part of unittest discovery or against a real ticket database.
"""
from pathlib import Path
import tempfile

from tickets import copilot
from tickets.db import initialize
from tickets.service import Store


def main():
    with tempfile.TemporaryDirectory(prefix='moldloft-glm-test-') as tmp:
        engine = initialize(Path(tmp) / 'fixture.db')
        try:
            store = Store(engine)
            store.mutate([{'op': 'create', 'fields': {'title': 'Synthetic API task', 'group_name': 'Fixture'}}])
            before = store.graph()
            prepared = copilot.prepare(store, [{'role': 'user', 'content': (
                'This is an authorized integration test with synthetic data. Prepare one proposal: '
                'rename ticket #1 to "Review API contract", create a child titled "Add API contract tests", '
                'and add a dependency from #1 to the new child. Use propose_ticket_changes for all three '
                'operations. Keep the explanation brief; do not ask for confirmation.')}], 1, [])
            proposal, text_chunks = None, 0
            for event in copilot.events(store, prepared):
                if event['type'] == 'text':
                    text_chunks += 1
                elif event['type'] == 'proposal':
                    proposal = event
                elif event['type'] == 'error':
                    raise RuntimeError(event['message'])
            assert proposal is not None, 'No complete proposal returned'
            assert store.graph() == before, 'Preview unexpectedly changed the fixture database'
            kinds = {op['op'] for op in proposal['operations']}
            assert {'create', 'update', 'edge_add'} <= kinds, 'Required proposal operations missing'
            store.mutate(proposal['operations'], revision=proposal['base_revision'])
            after = store.graph()
            assert len(after['tickets']) == 2 and len(after['edges']) == 1
            assert after['tickets'][0]['title'] == 'Review API contract'
            assert after['tickets'][1]['parent_id'] == 1, f'Synthetic proposal omitted containment: {proposal["operations"]}'
            assert after['edges'] == [{'blocker_id': 1, 'blocked_id': after['tickets'][1]['id']}], 'Dependency direction changed'
            question = copilot.prepare(store, [{'role': 'user', 'content':
                '用一句中文说明当前上下文里谁阻塞谁。只回答问题，不要提议任何修改。'}], 1, [])
            answer = list(copilot.events(store, question))
            assert any(e['type'] == 'text' and e['text'] for e in answer), 'No visible text streamed'
            assert not any(e['type'] in ('proposal', 'error') for e in answer), 'Question unexpectedly produced a proposal/error'
            assert store.graph() == after, 'Question changed fixture data'
            text_chunks += sum(e['type'] == 'text' for e in answer)
            print(f'GLM live PASS: {text_chunks} text chunks, {len(proposal["operations"])} operations; containment, direction, preview, atomic apply and question-only response verified in temporary DB.')
        finally:
            engine.dispose()


if __name__ == '__main__':
    main()
