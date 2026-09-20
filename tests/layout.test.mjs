import test from 'node:test';
import {computeLayout, registerLayoutStrategy, DEFAULT_LAYOUT, layoutStrategies} from '../src/tickets/static/layout-strategies.js';
import {fixtures, observations} from './layout-fixtures.mjs';
import {wrapText} from '../src/tickets/static/text-layout.js';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {project, groupKey, neighborhood} from '../src/tickets/static/layout.js';
const require = createRequire(import.meta.url);
const ELK = require('../src/tickets/static/vendor/elk/elk-api.js');
const {Worker} = require('../src/tickets/static/vendor/elk/elk-worker.min.js');
const elk = new ELK({workerFactory: () => new Worker()});
const layout = input => computeLayout(input, {engine: elk});
const ticket = (id, parent_id = null, group_name = 'Fixture') => ({id, parent_id, group_name,
  title: `Task ${id}`, status: 'open', priority: 'medium'});
const flatten = graph => [graph, ...(graph.children || []).flatMap(flatten)];

test('registered strategies share options and an owned projection; only default ships', async () => {
  assert.deepEqual(layoutStrategies().map(s => s.id), [DEFAULT_LAYOUT]);
  registerLayoutStrategy('test-only', {label: 'Test contract', defaults: {width: 10}, compute(input, {options}) {
    input.data.marker = 'owned'; return {...input, width: options.width, height: 20};
  }});
  const input = {id: 'root', children: [], edges: [], data: {marker: 'original'}}, before = structuredClone(input);
  assert.equal((await computeLayout(input, {strategy: 'test-only', options: {width: 30}})).width, 30);
  assert.deepEqual(input, before);
  assert.throws(() => registerLayoutStrategy('test-only', {compute() {}}), /duplicate/);
  await assert.rejects(computeLayout(input, {strategy: 'missing'}), /Unknown/);
  await assert.rejects(computeLayout(input), /unavailable/);
});

for (const [name, factory] of Object.entries(fixtures)) test(`${name}: finite non-overlapping bounds, edge labels and stable input ordering`, async () => {
  const graph = factory(), before = structuredClone(graph), input = project(graph);
  const result = await layout(input), measured = observations(result);
  assert.equal(measured.overlaps, 0);
  assert.equal(measured.labelNodeOverlaps, 0);
  assert.equal(measured.obscuredEndpoints, 0);
  assert.ok(measured.width > measured.height, 'overview should grow horizontally');
  const all = flatten(result), raw = all.flatMap(n => n.edges || []).flatMap(e => e.data.edges);
  assert.equal(raw.length, graph.edges.length + (graph.relations?.length || 0));
  assert.deepEqual(graph, before);
  assert.deepEqual(await layout(input), result);
  const shuffled = {...graph, tickets: [...graph.tickets].reverse(), notes: [...(graph.notes || [])].reverse(),
    edges: [...graph.edges].reverse(), relations: [...(graph.relations || [])].reverse()};
  assert.deepEqual(await layout(project(shuffled)), result);
  for (const edge of all.flatMap(n => n.edges || [])) {
    assert.ok(edge.sections?.length, 'every edge is routed');
    for (const label of edge.labels || []) assert.ok(Number.isFinite(label.x) && Number.isFinite(label.y));
  }
  console.log(name, measured);
});

test('labels wrap at English word boundaries and bound CJK previews', () => {
  const measure = s => [...s].length;
  assert.deepEqual(wrapText('Review dependency contract', 17, measure), ['Review dependency', 'contract']);
  assert.deepEqual(wrapText('Review dependency contract and more', 17, measure), ['Review dependency', 'contract and more']);
  assert.deepEqual(wrapText('验证尚未完成的假设并保留不确定方向', 10, measure, 1), ['验证尚未完成的假设…']);
  assert.deepEqual(wrapText('averylongidentifier', 10, measure), ['averylong…']);
  assert.ok(wrapText('A long description overflowing the last line', 10, measure).every(s => s.length <= 10));
});

test('note lookup finds either abstract without changing source content or relations', () => {
  const graph = {tickets: [], edges: [], relations: [], notes: [
    {id: 'n-audio', title: 'Audio candidate', abstract_zh: '用提示音检验在场感。', abstract_en: 'Try an event chime before TTS.'},
    {id: 'n-legacy', title: 'Legacy question'},
  ]};
  const before = structuredClone(graph);
  for (const search of ['在场感', 'EVENT CHIME', '  提示音  ']) {
    const result = project(graph, new Set(), {search});
    assert.deepEqual(flatten(result).filter(n => n.data?.note).map(n => n.data.note.id), ['n-audio']);
  }
  assert.equal(project(graph, new Set(), {search: 'missing phrase'}).children.length, 0);
  assert.equal(flatten(project(graph, new Set(), {search: 'Legacy'})).filter(n => n.data?.note).length, 1);
  assert.deepEqual(graph, before);
});

test('typed links retain semantics through collapse, filtering and local neighborhoods', async () => {
  const graph = {tickets: [ticket(1), ticket(2), ticket(3, null, 'Other')], edges: [{blocker_id: 1, blocked_id: 2}],
    notes: [{id: 'n-premise', title: 'Premise', kind: 'assumption', state: 'proposed'}, {id: 'n-other', title: 'Other', kind: 'idea', state: 'open'}],
    relations: [
      {source: 'note:n-premise', target: 'ticket:1', type: 'assumes', owner: 'n-premise'},
      {source: 'note:n-premise', target: 'ticket:2', type: 'relates', owner: 'n-premise'},
      {source: 'note:n-other', target: 'note:n-premise', type: 'relates', owner: 'n-other'},
    ]};
  const input = project(graph, new Set([groupKey('Fixture')]));
  assert.deepEqual(input.edges.map(e => e.data.type).sort(), ['assumes', 'relates', 'relates']);
  const result = await layout(input);
  assert.equal(flatten(result).filter(n => n.data?.note).length, 2);
  assert.deepEqual([...neighborhood(graph, 't:1', 1)].sort(), ['n:n-premise', 't:1', 't:2']);
  const local = project(graph, new Set(), {focus: 't:1', depth: 1});
  assert.equal(flatten(local).filter(n => n.data?.note).length, 1);
  assert.equal(flatten(local).filter(n => n.data?.ticket).length, 2);
  const noNotes = project(graph, new Set(), {showNotes: false});
  assert.equal(noNotes.edges.length, 1);
  const requiresOnly = project(graph, new Set(), {focus: 't:1', depth: 1, types: new Set(['requires'])});
  assert.equal(flatten(requiresOnly).filter(n => n.data?.note).length, 0);
  assert.equal(graph.relations.length, 3, 'projection does not edit source relations');
});

test('local traversal keeps upstream and downstream paths separate at forks and joins', () => {
  const graph = {tickets: Array.from({length: 9}, (_, i) => ticket(i + 1)), edges: [
    [1, 2], [2, 3], [3, 4], [4, 5], [5, 6], [2, 7], [8, 4], [6, 9],
  ].map(([blocker_id, blocked_id]) => ({blocker_id, blocked_id}))};
  const before = structuredClone(graph);
  const visit = (direction, depth = 3) => [...neighborhood(graph, 't:3', depth, null, direction)].sort();
  assert.deepEqual(visit('upstream'), ['t:1', 't:2', 't:3']);
  assert.deepEqual(visit('downstream'), ['t:3', 't:4', 't:5', 't:6']);
  assert.deepEqual(visit('both'), ['t:1', 't:2', 't:3', 't:4', 't:5', 't:6']);
  assert.deepEqual(visit(undefined), visit('both'), 'the default still preserves direction');
  assert.deepEqual(visit('both', 1), ['t:2', 't:3', 't:4']);
  assert.deepEqual(visit('both', 2), ['t:1', 't:2', 't:3', 't:4', 't:5']);
  assert.deepEqual(visit('both', 99), visit('both', 3), 'depth remains bounded');
  const reordered = {...graph, edges: [...graph.edges].reverse()};
  assert.deepEqual([...neighborhood(reordered, 't:3', 3)].sort(), visit('both'));
  assert.deepEqual(graph, before, 'local traversal never rewrites execution data');
});

test('typed local traversal respects arrows, undirected context, filters and cycles', () => {
  const relation = (source, target, type, missing = false) => ({source, target, type, missing});
  const graph = {tickets: [ticket(1), ticket(2)], edges: [{blocker_id: 1, blocked_id: 2}], relations: [
    relation('note:n-premise', 'ticket:1', 'assumes'),
    relation('note:n-evidence', 'note:n-premise', 'supports'),
    relation('note:n-idea', 'note:n-evidence', 'option'),
    relation('note:n-premise', 'note:n-side', 'option'),
    relation('note:n-evidence', 'note:n-missing', 'relates', true),
  ]};
  assert.deepEqual([...neighborhood(graph, 't:1', 3, null, 'upstream')].sort(),
    ['n:n-evidence', 'n:n-idea', 'n:n-premise', 't:1']);
  assert.deepEqual([...neighborhood(graph, 'n:n-idea', 3, null, 'downstream')].sort(),
    ['n:n-evidence', 'n:n-idea', 'n:n-premise', 'n:n-side', 't:1']);
  assert.deepEqual([...neighborhood(graph, 't:1', 3, new Set(['requires']))].sort(), ['t:1', 't:2']);
  assert.deepEqual([...neighborhood(graph, 't:1', 3, new Set(['assumes']), 'upstream')].sort(), ['n:n-premise', 't:1']);
  assert.deepEqual([...neighborhood(graph, 't:1', 3, new Set())], ['t:1']);

  graph.relations.push(relation('note:n-premise', 'note:n-evidence', 'relates'));
  for (const direction of ['upstream', 'downstream', 'both']) {
    for (const focus of ['n:n-premise', 'n:n-evidence']) {
      assert.deepEqual([...neighborhood(graph, focus, 3, new Set(['relates']), direction)].sort(), ['n:n-evidence', 'n:n-premise']);
    }
  }
  assert.deepEqual([...neighborhood(graph, 'n:n-evidence', 3, null, 'both')].sort(),
    ['n:n-evidence', 'n:n-idea', 'n:n-premise', 'n:n-side', 't:1', 't:2'],
    'a node reached in one direction must still be explored independently in the other');
});

test('directional projection retains containment without traversing siblings', () => {
  const graph = {tickets: [ticket(1), ticket(2, 1), ticket(3, 1), ticket(4), ticket(5)],
    edges: [{blocker_id: 4, blocked_id: 2}, {blocker_id: 2, blocked_id: 5}]};
  const ids = filter => flatten(project(graph, new Set(), filter)).filter(n => n.data?.ticket).map(n => n.data.ticket.id).sort();
  assert.deepEqual(ids({focus: 't:2', depth: 3, direction: 'upstream'}), [1, 2, 4]);
  assert.deepEqual(ids({focus: 't:2', depth: 3, direction: 'downstream'}), [1, 2, 5]);
  assert.deepEqual(ids({focus: 't:2', depth: 3}), [1, 2, 4, 5]);
  assert.deepEqual(ids({direction: 'upstream'}), [1, 2, 3, 4, 5], 'global projection ignores local direction');
  const collapsed = project(graph, new Set(['t:1']), {focus: 't:2', depth: 3, direction: 'upstream'});
  assert.deepEqual(collapsed.edges.map(e => [e.sources[0], e.targets[0]]), [['t:4', 't:1']]);
});

test('ELK handles parent endpoints in either dependency direction', async () => {
  for (const [blocker_id, blocked_id] of [[1, 2], [2, 1]]) {
    const input = project({tickets: [ticket(1), ticket(2, 1)], edges: [{blocker_id, blocked_id}]});
    const result = await layout(input);
    assert.equal(flatten(result).flatMap(n => n.edges || []).length, 1);
    for (const n of flatten(result)) assert.ok(Number.isFinite(n.width) && Number.isFinite(n.height));
    assert.deepEqual(await layout(input), result, 'same graph should produce the same layout');
  }
});

test('collapsed quotient can have a visual cycle without changing the true DAG', async () => {
  const input = project({tickets: [ticket(1, null, 'A'), ticket(2, null, 'B'), ticket(3, null, 'B'), ticket(4, null, 'A')],
    edges: [{blocker_id: 1, blocked_id: 2}, {blocker_id: 3, blocked_id: 4}]}, new Set([groupKey('A'), groupKey('B')]));
  const result = await layout(input);
  const raw = flatten(result).flatMap(n => n.edges || []).flatMap(e => e.data.edges);
  assert.equal(result.children.length, 2);
  assert.deepEqual(raw, [{blocker_id: 1, blocked_id: 2}, {blocker_id: 3, blocked_id: 4}]);
});

test('500 tickets and 1000 dependencies retain finite bounds and every edge', async () => {
  const tickets = Array.from({length: 500}, (_, index) => {
    const first = Math.floor(index / 50) * 50 + 1;
    return ticket(index + 1, index + 1 === first ? null : first, `Group ${Math.floor(index / 50)}`);
  });
  const pairs = new Set();
  let seed = 19;
  while (pairs.size < 1000) {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    const a = seed % 499 + 1;
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    const b = a + 1 + seed % (500 - a);
    pairs.add(`${a},${b}`);
  }
  const edges = [...pairs].map(pair => { const [blocker_id, blocked_id] = pair.split(',').map(Number); return {blocker_id, blocked_id}; });
  const start = performance.now();
  const result = await layout(project({tickets, edges}));
  const nodes = flatten(result);
  assert.equal(nodes.filter(n => n.data?.ticket).length, 500);
  assert.equal(nodes.flatMap(n => n.edges || []).flatMap(e => e.data.edges).length, 1000);
  for (const n of nodes) assert.ok(Number.isFinite(n.width) && Number.isFinite(n.height));
  console.log(`500/1000 layout: ${(performance.now() - start).toFixed(0)} ms; worker runs off the browser UI thread.`);
});
