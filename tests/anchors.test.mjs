import assert from 'node:assert/strict';
import test from 'node:test';
import {setImmediate} from 'node:timers/promises';
import {AnchorPanel} from '../src/moldloft/static/anchors.js';

class Element extends EventTarget {
  children = [];
  value = '';
  get valueAsNumber() { return Number(this.value); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute(key, value) { this[key] = value; }
  querySelectorAll() { return []; }
}

test('suggestion chips open a prefilled file dialog and only submit pins the target', async t => {
  const nodes = new Map();
  const control = name => {
    if (!nodes.has(name)) nodes.set(name, new Element());
    return nodes.get(name);
  };
  globalThis.document = {getElementById: control, createElement: () => new Element()};
  t.after(() => { delete globalThis.document; });
  const form = control('form');
  form.elements = {namedItem: control};
  form.querySelector = control;
  form.reset = () => { control('ref').value = 'HEAD'; control('kind').value = 'file'; };
  const dialog = control('anchor-dialog');
  dialog.querySelector = control;
  dialog.showModal = () => { dialog.open = true; };
  dialog.close = () => { dialog.open = false; };
  const run = fn => fn();
  const target = {type: 'ticket', id: 1};
  const graph = {id: 'demo', repos: [], tickets: [{id: 1}], anchors: []};
  const mutations = [];
  const store = {graph, selection: target, mutate: ops => mutations.push(...ops), refresh() {}, verifyTarget() {}};
  const request = t.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(options.method, 'GET');
    if (url === '/api/git/docs/refs') return Response.json({head: 'abc123', branches: [], commits: []});
    assert.equal(url, '/api/graphs/demo/anchor-suggestions?target=1');
    return Response.json({suggestions: [{repo: 'docs', path: 'guide.md', start_line: 2, end_line: 4}]});
  });
  const section = new Element();
  const panel = new AnchorPanel(store, {run, guard: run}, section);
  panel.render(target);
  assert.equal(request.mock.callCount(), 0);
  graph.repos = ['app', 'docs'];
  panel.render(target);
  await setImmediate();
  const [chip] = section.children.at(-1).children;
  assert.equal(chip.textContent, '+ docs:guide.md:2-4');
  chip.dispatchEvent(new Event('click'));
  assert.equal(dialog.open, true);
  const fields = ['repo', 'kind', 'ref', 'path', 'start_line', 'end_line'];
  assert.deepEqual(fields.map(name => control(name).value), ['docs', 'file', 'HEAD', 'guide.md', 2, 4]);
  assert.deepEqual(mutations, []);
  form.dispatchEvent(new Event('submit'));
  await setImmediate();
  assert.deepEqual(mutations, [{op: 'anchor_add', target_type: 'ticket', target_id: 1,
    anchor: {repo: 'docs', kind: 'file', ref: 'HEAD', path: 'guide.md', start_line: 2, end_line: 4, symbol: '', claim: ''}}]);
  assert.equal(dialog.open, false);
});
