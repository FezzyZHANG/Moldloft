import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import test from 'node:test';
import {computeLayout} from '../src/moldloft/static/compact-layered.js';
import {groupKey, neighborhood, noteKey, previewOverlay, project, ticketKey} from '../src/moldloft/static/layout.js';
import {wrapText} from '../src/moldloft/static/text-layout.js';

const require = createRequire(import.meta.url);
const ELK = require('../src/moldloft/static/vendor/elk/elk-api.js');
const {Worker} = require('../src/moldloft/static/vendor/elk/elk-worker.min.js');
const engine = new ELK({workerFactory: () => new Worker()});
const compute = input => computeLayout(input, {engine});
const ticket = (id, parent_id = null, group_name = 'Hull') => ({id, parent_id, group_name, status: 'open', title: `Task ${id}`});
const note = (id, in_graph = true) => ({id, in_graph, title: id, kind: 'note', state: 'active'});
const edge = (blocker_id, blocked_id) => ({blocker_id, blocked_id});
const link = (source_note_id, relation, target_type, target_id) => ({source_note_id, relation, target_type, target_id});
const graph = (tickets, edges, notes = [], note_links = []) => ({tickets, edges, notes, note_links, anchors: []});
const flatten = node => [node, ...(node.children || []).flatMap(flatten)];

test('upstream and downstream remain separate at forks and joins', () => {
  const input = graph(Array.from({length: 9}, (_, i) => ticket(i + 1)),
    [[1, 2], [2, 3], [3, 4], [4, 5], [5, 6], [2, 7], [8, 4], [6, 9]].map(pair => edge(...pair)));
  const visit = (direction, depth = 3) => [...neighborhood(input, ticketKey(3), depth, null, direction)].sort();
  assert.deepEqual(visit('upstream'), ['t:1', 't:2', 't:3']);
  assert.deepEqual(visit('downstream'), ['t:3', 't:4', 't:5', 't:6']);
  assert.deepEqual(visit('both'), ['t:1', 't:2', 't:3', 't:4', 't:5', 't:6']);
  assert.deepEqual(visit('both', 1), ['t:2', 't:3', 't:4']);
});

test('relates is undirected and reference notes never bridge the graph', () => {
  const input = graph([ticket(1), ticket(2)], [edge(1, 2)], [note('n-near'), note('n-index', false)], [
    link('n-near', 'relates', 'ticket', 1), link('n-index', 'relates', 'note', 'n-near'),
    link('n-index', 'relates', 'ticket', 2),
  ]);
  for (const direction of ['upstream', 'downstream', 'both']) {
    const types = new Set(['relates']);
    assert.deepEqual([...neighborhood(input, ticketKey(1), 3, types, direction)], ['t:1', 'n:n-near']);
    assert.deepEqual([...neighborhood(input, noteKey('n-near'), 3, types, direction)], ['n:n-near', 't:1']);
    assert.equal(neighborhood(input, noteKey('n-index'), 3, types, direction).size, 0);
  }
  assert.deepEqual([...neighborhood(input, ticketKey(1), 1, new Set(['requires']), 'downstream')], ['t:1', 't:2']);
});

test('projection retains hierarchy, bundles typed edges, and rolls drift up to collapsed owners', () => {
  const input = graph([ticket(1), ticket(2, 1), ticket(3, 1), ticket(4, null, 'Other')],
    [edge(2, 4), edge(3, 4), edge(2, 3)], [note('n-near'), note('n-index', false)], [
      link('n-near', 'relates', 'ticket', 2), link('n-near', 'relates', 'ticket', 3),
    ]);
  const before = structuredClone(input);
  const drift = new Map([
    [1, {target_type: 'ticket', target_id: 2, status: 'drifted'}],
    [2, {target_type: 'ticket', target_id: 3, status: 'missing'}],
    [3, {target_type: 'note', target_id: 'n-near', status: 'dirty'}],
  ]);
  const output = project(input, new Set([ticketKey(1)]), {drift});
  const nodes = flatten(output);
  assert.deepEqual(nodes.find(n => n.id === ticketKey(1)).data.drift, {drifted: 1, missing: 1});
  assert.equal(nodes.find(n => n.id === groupKey('Hull')).children[0].id, ticketKey(1));
  assert.ok(output.children.some(n => n.id === noteKey('n-near')));
  assert.ok(!nodes.some(n => n.id === noteKey('n-index')));
  assert.equal(output.edges.length, 2);
  assert.deepEqual(output.edges.map(e => [e.data.type, e.data.edges.length]).sort(), [['relates', 2], ['requires', 2]]);
  const local = flatten(project(input, new Set(), {search: '#3', showNotes: false}));
  assert.deepEqual(local.filter(n => n.data?.ticket).map(n => n.data.ticket.id), [1, 3]);
  assert.deepEqual(input, before);
});

test('proposal overlay consumes backend change identities and retains deletions', () => {
  const original = graph([ticket(1), ticket(2)], [edge(1, 2)],
    [note('n-old'), note('n-edited')], [link('n-old', 'relates', 'ticket', 1)]);
  const preview = graph([{...ticket(1), title: 'Changed'}, ticket('new:next')],
    [edge(1, 'new:next')], [{...note('n-edited'), title: 'Edited'}, note('n-draft')],
    [link('n-draft', 'relates', 'ticket', 'new:next')]);
  const changes = [
    ['tickets', 1, 'update'], ['tickets', 2, 'delete'], ['tickets', 'new:next', 'create'],
    ['notes', 'n-old', 'delete'], ['notes', 'n-draft', 'create'], ['notes', 'n-edited', 'update'],
    ['edges', [1, 2], 'delete'], ['edges', [1, 'new:next'], 'create'],
    ['note_links', ['n-old', 'relates', 'ticket', 1], 'delete'],
    ['note_links', ['n-draft', 'relates', 'ticket', 'new:next'], 'create'],
  ].map(([entity, id, action]) => ({entity, id, action, fields: {}}));
  const output = previewOverlay(original, {preview, changes});
  assert.deepEqual(output.tickets.map(t => [t.id, t._proposal]),
    [[1, 'changed'], ['new:next', 'added'], [2, 'deleted']]);
  assert.deepEqual(output.notes.map(n => n._proposal), ['changed', 'added', 'deleted']);
  assert.deepEqual(output.edges.map(e => e._proposal), ['added', 'removed']);
  assert.deepEqual(output.note_links.map(l => l._proposal), ['added', 'removed']);
  assert.equal(preview.tickets.length, 2);
  assert.equal(original.tickets[0].title, 'Task 1');
  assert.equal(previewOverlay(original, {preview: null, changes: []}), original);
});

test('SVG labels wrap words and CJK with a visible ellipsis', () => {
  const measure = text => [...text].length;
  assert.deepEqual(wrapText('Review dependency contract', 17, measure), ['Review dependency', 'contract']);
  assert.deepEqual(wrapText('验证尚未完成的假设并保留不确定方向', 10, measure, 1), ['验证尚未完成的假设…']);
  assert.deepEqual(wrapText('averylongidentifier more', 10, measure), ['averylong…', 'more']);
  assert.ok(wrapText('A long description overflowing the last line', 10, measure).every(s => s.length <= 10));
});

function verifyGeometry(output) {
  for (const node of flatten(output)) {
    assert.ok(Number.isFinite(node.width) && Number.isFinite(node.height), node.id);
    if (node !== output) assert.ok(Number.isFinite(node.x) && Number.isFinite(node.y), node.id);
  }
  for (const edge of output.edges) {
    assert.ok(edge.sections?.length, edge.id);
    for (const section of edge.sections) {
      for (const point of [section.startPoint, ...(section.bendPoints || []), section.endPoint]) {
        assert.ok(Number.isFinite(point.x) && Number.isFinite(point.y), edge.id);
      }
    }
  }
}

test('vendored layout is deterministic for compound parent endpoints and packed components', async () => {
  const tickets = [ticket(1), ticket(2, 1), ticket(3, null, 'Fittings'), ticket(4, null, 'Deck')];
  for (const dependency of [edge(1, 2), edge(2, 1)]) {
    const input = project(graph(tickets, [dependency]));
    const before = structuredClone(input);
    const output = await compute(input);
    verifyGeometry(output);
    assert.equal(output.edges.length, 1);
    assert.ok(output.children.every(node => node.x >= 0 && node.y >= 0));
    assert.ok(output.children.some(node => node.x <= 8 && node.y <= 8));
    assert.deepEqual(await compute(project(graph([...tickets].reverse(), [dependency]))), output);
    assert.deepEqual(input, before);
  }
});

test('a collapsed-group cycle keeps both routed dependencies', async () => {
  const input = graph([ticket(1, null, 'A'), ticket(2, null, 'B'), ticket(3, null, 'B'), ticket(4, null, 'A')],
    [edge(1, 2), edge(3, 4)]);
  const output = await compute(project(input, new Set([groupKey('A'), groupKey('B')])));
  verifyGeometry(output);
  assert.equal(output.children.length, 2);
  assert.deepEqual(output.edges.flatMap(e => e.data.edges), input.edges);
});

test('500 tickets and 1000 dependencies retain finite geometry and every edge', {timeout: 15000}, async () => {
  const tickets = Array.from({length: 500}, (_, i) => {
    const parent = Math.floor(i / 50) * 50 + 1;
    return ticket(i + 1, i + 1 === parent ? null : parent, `Group ${parent}`);
  });
  const pairs = new Map();
  let seed = 19;
  while (pairs.size < 1000) {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    const a = seed % 499 + 1;
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    const b = a + 1 + seed % (500 - a);
    pairs.set(`${a}:${b}`, edge(a, b));
  }
  const output = await compute(project(graph(tickets, [...pairs.values()])));
  verifyGeometry(output);
  assert.equal(flatten(output).filter(n => n.data?.ticket).length, 500);
  assert.equal(output.edges.reduce((count, e) => count + e.data.edges.length, 0), 1000);
});
