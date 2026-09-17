"""Paid, opt-in GLM test with bilingual synthetic abstracts; no personal data."""
from pathlib import Path
import tempfile

from tickets import copilot
from tickets.db import initialize
from tickets.notes import Notebook
from tickets.service import Store


def main():
    with tempfile.TemporaryDirectory(prefix='moldloft-glm-notes-') as tmp:
        engine = initialize(Path(tmp) / 'fixture.db')
        try:
            book = Notebook(Path(tmp) / 'notes', engine)
            store = Store(engine, notes=book)
            store.mutate([{'op': 'create', 'fields': {'title': 'Synthetic editor task'}}])
            book.write('n-premise', {'title': 'Single operator', 'kind': 'assumption', 'state': 'proposed',
                'body': 'We have not verified that one operator is sufficient.',
                'abstract_zh': '单人编辑仍是待验证前提；先用 draft recovery 实验检验草稿恢复。',
                'abstract_en': 'Examine the single-operator premise by testing recovery of unsaved changes; no result is established.',
                'relations': [{'type': 'assumes', 'target': 'ticket:1'}]}, None, {1})
            before = store.graph()
            prepared = copilot.prepare(store, [{'role': 'user', 'content':
                'This is an authorized synthetic integration test. Use the attached premise to edit ONLY '
                'the technical notes of ticket #1 using exactly this English sentence template: '
                '"Evaluate <experiment>; the single-operator premise is unverified." Replace <experiment> with '
                'the two-word English experiment name embedded in the attached Chinese abstract. '
                'Return a preview using propose_ticket_changes. Do not create a ticket, add a dependency, or edit a note.'}],
                None, [], 'n-premise')
            proposal, chunks = None, 0
            for event in copilot.events(store, prepared):
                if event['type'] == 'text':
                    chunks += 1
                elif event['type'] == 'proposal':
                    proposal = event
                elif event['type'] == 'error':
                    raise RuntimeError(event['message'])
            assert proposal is not None
            assert store.graph() == before
            assert all(op['op'] == 'update' and op['ticket_id'] == 1 and set(op['fields']) == {'tech_notes'} for op in proposal['operations'])
            store.mutate(proposal['operations'], revision=proposal['base_revision'], notes_revision=proposal['notes_revision'])
            after = store.graph()
            assert after['notes'] == before['notes'] and after['edges'] == []
            assert after['tickets'][0]['tech_notes'] == 'Evaluate draft recovery; the single-operator premise is unverified.'
            print(f'GLM notes PASS: {chunks} text chunks; bilingual context, Chinese abstract extraction, unchanged premise, preview and guarded ticket-only apply.')
        finally:
            engine.dispose()


if __name__ == '__main__':
    main()
