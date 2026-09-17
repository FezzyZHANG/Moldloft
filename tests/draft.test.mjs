import {test} from 'node:test';
import assert from 'node:assert/strict';
import {TicketDraft, AttentionSaver} from '../src/tickets/static/draft.js';
import {project, previewOverlay} from '../src/tickets/static/layout.js';
const ticket = (id = 1, parent = null, group = 'work') => ({id, title: 'Initial', description: '', tech_notes: '', group_name: group, parent_id: parent, priority: 'medium', status: 'open', version: 1});

test('changes stay local until attention leaves; duplicate leaves save once', async () => {
  let saves = 0, release;
  const draft = new TicketDraft(async (base, patch) => { saves++; await new Promise(r => release = r); return {...base, ...patch, version: 2}; });
  draft.load(ticket()); draft.set('title', 'Edited');
  assert.equal(saves, 0);
  const attention = new AttentionSaver(draft, () => false);
  const first = attention.leave(), second = attention.leave();
  await Promise.resolve(); assert.equal(saves, 1); release(); await Promise.all([first, second]);
  assert.equal(draft.dirty, false);
});

test('typing during save remains dirty after the older acknowledgement', async () => {
  let release;
  const draft = new TicketDraft(async (base, patch) => { await new Promise(r => release = r); return {...base, ...patch, version: 2}; });
  draft.load(ticket()); draft.set('title', 'First'); const saving = draft.flush(); await Promise.resolve();
  draft.set('title', 'Second'); release(); await saving;
  assert.equal(draft.values.title, 'Second'); assert.equal(draft.base.title, 'First'); assert.equal(draft.dirty, true);
});

test('composition delays save and a dependent action awaits acknowledgement', async () => {
  let saved = false;
  const draft = new TicketDraft(async (base, patch) => { saved = true; return {...base, ...patch, version: 2}; });
  draft.load(ticket()); draft.set('title', '中文');
  const attention = new AttentionSaver(draft, () => false); attention.composing = true;
  await attention.leave(); assert.equal(saved, false);
  await attention.compositionEnd();
  await attention.beforeAction(() => assert.equal(saved, true));
});

test('conflict retains draft and prevents dependent chat send', async () => {
  const draft = new TicketDraft(async () => { const e = new Error('Conflict'); e.status = 409; e.data = {current: {...ticket(), title: 'Remote', version: 2}}; throw e; });
  draft.load(ticket()); draft.set('title', 'Local');
  const attention = new AttentionSaver(draft, () => false); let sent = false;
  await assert.rejects(attention.beforeAction(() => { sent = true; }));
  assert.equal(sent, false); assert.equal(draft.values.title, 'Local'); assert.equal(draft.state, 'conflict');
});

test('collapsed projection retains two-way summaries without changing source DAG', () => {
  const graph = {revision: 1, tickets: [ticket(1, null, 'A'), ticket(2, null, 'B'), ticket(3, null, 'A')],
    edges: [{blocker_id: 1, blocked_id: 2}, {blocker_id: 2, blocked_id: 3}], links: []};
  const copy = structuredClone(graph); const view = project(graph, new Set(['g:A', 'g:B']));
  assert.equal(view.edges.length, 2); assert.deepEqual(graph, copy);
  assert.equal(view.children[0].children, undefined);
});

test('nested parent nodes and previews never modify the server snapshot', () => {
  const graph = {revision: 1, tickets: [ticket(1), ticket(2, 1)], edges: [], links: []};
  const view = project(graph); assert.equal(view.children[0].children[0].children[0].id, 't:2');
  const p = {preview: {...graph, tickets: [{...ticket(1), title: 'AI'}, ticket(2, 1)]}, changes: [{kind: 'updated', ticket_id: 1}]};
  const displayed = previewOverlay(graph, p);
  assert.equal(displayed.tickets[0].title, 'AI'); assert.equal(graph.tickets[0].title, 'Initial');
});

test('mixed preview preserves alias relations through filtering and collapsed groups', () => {
  const base = {tickets: [ticket()], edges: [], notes: [], relations: []};
  const proposed = {preview: {...base, tickets: [...base.tickets, ticket('new:work', null, 'new-group')],
    notes: [{id: 'n-premise', title: 'Premise', kind: 'assumption', state: 'proposed'}],
    relations: [{source: 'note:n-premise', target: 'ticket:new:work', owner: 'n-premise', type: 'assumes', missing: false}]},
    changes: [{kind: 'created', ticket_id: 'new:work'}, {kind: 'note', note_id: 'n-premise'}]};
  const before = structuredClone({base, proposed});
  const overlay = previewOverlay(base, proposed);
  assert.equal(overlay.notes[0]._proposal, 'changed');
  assert.equal(overlay.tickets[1]._proposal, 'changed');
  for (const filter of [{}, {group: 'new-group'}, {status: 'open'}, {focus: 'n:n-premise', depth: 1}]) {
    const view = project(overlay, new Set(), filter);
    assert.equal(view.edges.length, 1, 'the premise relation remains visible');
    assert.deepEqual(view.edges[0].sources, ['n:n-premise']);
    assert.deepEqual(view.edges[0].targets, ['t:new:work']);
  }
  const collapsed = project(overlay, new Set(['g:new-group']), {group: 'new-group'});
  assert.deepEqual(collapsed.edges[0].targets, ['g:new-group']);
  assert.deepEqual({base, proposed}, before, 'preview annotations never change source or stored proposal');
});
