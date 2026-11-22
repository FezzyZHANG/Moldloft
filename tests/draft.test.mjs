import assert from 'node:assert/strict';
import test from 'node:test';
import {AttentionSaver, ItemDraft} from '../src/moldloft/static/draft.js';

const initial = {title: 'Original', body: 'Text', in_graph: true, version: 1, revision: 3};
const saved = (item, fields) => ({...item, ...fields, version: item.version + 1, revision: item.revision + 1});
function draftWith(save) {
  const draft = new ItemDraft(save, () => {}, ['title', 'body', 'in_graph']);
  draft.load(initial);
  return draft;
}

test('draft tracks only configured changed fields and preserves JSON types', async () => {
  const draft = draftWith(async (base, fields) => {
    assert.equal(base.version, 1);
    assert.deepEqual(fields, {in_graph: false});
    return saved(base, fields);
  });
  draft.set('title', 'Draft');
  assert.equal(draft.dirty, true);
  draft.set('title', 'Original');
  assert.equal(draft.dirty, false);
  draft.set('in_graph', false);
  assert.equal(draft.state, 'dirty');
  await draft.flush();
  assert.equal(draft.state, 'clean');
  assert.equal(draft.base.version, 2);
  assert.equal(draft.values.in_graph, false);
});

test('duplicate leave events share a save and catch edits made during the request', async () => {
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const calls = [];
  const draft = draftWith(async (base, fields) => {
    calls.push([base.version, fields]);
    await gate;
    return saved(base, fields);
  });
  draft.set('title', 'Local');
  const attention = new AttentionSaver(draft, () => false);
  const first = attention.leave();
  assert.equal(attention.leave(), first);
  await Promise.resolve();
  await Promise.resolve();
  assert.equal(draft.state, 'saving');
  draft.set('body', 'Also edited');
  release();
  await first;
  assert.deepEqual(calls, [[1, {title: 'Local'}], [2, {body: 'Also edited'}]]);
  assert.equal(draft.state, 'clean');
});

test('attention waits for IME composition and saves the completed input before navigating', async () => {
  const calls = [];
  const draft = draftWith(async (base, fields) => {
    calls.push(fields.title);
    return saved(base, fields);
  });
  const attention = new AttentionSaver(draft, () => false);
  attention.compositionStart();
  draft.set('title', 'pin');
  const leave = attention.leave();
  const action = attention.beforeAction(() => calls.push('navigate'));
  await Promise.resolve();
  assert.deepEqual(calls, []);
  attention.compositionEnd();
  attention.compositionStart();
  await Promise.resolve();
  assert.deepEqual(calls, []);
  draft.set('title', '拼音');
  attention.compositionEnd();
  await Promise.all([leave, action]);
  assert.deepEqual(calls, ['拼音', 'navigate']);
});

test('409 and snapshot conflicts retain edits and can rebase onto a newer version', async () => {
  let current = {...initial, title: 'Server title', version: 2, revision: 4};
  const draft = draftWith(async (base, fields) => {
    if (base.version !== current.version) {
      throw Object.assign(new Error('Version conflict'), {status: 409, data: {current}});
    }
    return saved(base, fields);
  });
  draft.set('body', 'Local body');
  await assert.rejects(draft.flush(), /Version conflict/);
  assert.equal(draft.state, 'conflict');
  assert.deepEqual(draft.conflict.current, current);
  assert.deepEqual(draft.patch, {body: 'Local body'});
  current = {...current, title: 'Newer title', version: 3, revision: 5};
  draft.observe(current);
  assert.equal(draft.conflict.current.version, 3);
  draft.rebase(current);
  assert.equal(draft.values.title, 'Newer title');
  assert.deepEqual(draft.patch, {body: 'Local body'});
  await draft.flush();
  assert.equal(draft.base.version, 4);
  assert.equal(draft.state, 'clean');
});

test('failed save keeps its draft visible and blocks the action until retry succeeds', async () => {
  let fail = true;
  const draft = draftWith(async (base, fields) => {
    if (fail) throw new Error('Offline');
    return saved(base, fields);
  });
  const attention = new AttentionSaver(draft, () => false);
  draft.set('title', 'Still here');
  let moved = false;
  await assert.rejects(attention.beforeAction(() => { moved = true; }), /Offline/);
  assert.equal(moved, false);
  assert.equal(draft.state, 'error');
  assert.deepEqual(draft.patch, {title: 'Still here'});
  draft.observe(initial);
  assert.equal(draft.state, 'error');
  fail = false;
  await attention.beforeAction(() => { moved = true; });
  assert.equal(moved, true);
  assert.equal(draft.state, 'clean');
});
