"""Synthetic browser fixture; its model emits deterministic previews without a call."""
from pathlib import Path
import json
import tempfile
import time

from tickets.demo import create_demo_app


def model(**kwargs):
    context = json.loads(kwargs['messages'][-1]['content'].split('\n', 1)[1])
    ticket = next((t for t in context['tickets'] if t['id'] == 3), context['tickets'][0])
    yield {'type': 'text', 'text': '已准备修改预览。'}
    for _ in range(8):
        if kwargs['cancel'].is_set():
            return
        time.sleep(0.1)
    yield {'type': 'tool', 'name': 'propose_ticket_changes', 'arguments': {'operations': [
        {'op': 'update', 'ticket_id': ticket['id'], 'fields': {'title': 'Reviewed API contract'}},
        {'op': 'create', 'temp_id': 'new:review', 'fields': {'title': 'Review fixture', 'parent_id': ticket['id']}}
    ]}}
    yield {'type': 'done'}


if __name__ == '__main__':
    from tickets.server import run
    with tempfile.TemporaryDirectory(prefix='moldloft-tickets-browser-') as tmp:
        run(create_demo_app(Path(tmp) / 'test.db', copilot_streamer=model), port=8792)
