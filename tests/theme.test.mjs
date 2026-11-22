import assert from 'node:assert/strict';
import test from 'node:test';
import {applyTheme, DEFAULT_THEME, loadTheme, saveTheme, ThemeManager, THEMES} from '../src/moldloft/static/theme.js';

function memoryStorage() {
  const data = new Map();
  return {
    getItem: key => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => data.set(key, String(value)),
  };
}

class MockSelect extends EventTarget {
  constructor() {
    super();
    this.options = [];
    this.value = '';
  }
  replaceChildren(...nodes) { this.options = nodes; }
}

test('loadTheme defaults to endfield and ignores unknown or failing storage', () => {
  const storage = memoryStorage();
  assert.equal(DEFAULT_THEME, 'endfield');
  assert.equal(loadTheme(storage), 'endfield');
  storage.setItem('moldloft.theme.v1', 'loft');
  assert.equal(loadTheme(storage), 'loft');
  storage.setItem('moldloft.theme.v1', 'unknown-theme');
  assert.equal(loadTheme(storage), 'endfield');
  const broken = {getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); }};
  assert.equal(loadTheme(broken), 'endfield');
  assert.equal(saveTheme('loft', broken), 'loft');
});

test('applyTheme validates theme IDs and writes dataset.theme', () => {
  const root = {dataset: {}};
  assert.equal(applyTheme('loft', root), 'loft');
  assert.equal(root.dataset.theme, 'loft');
  assert.equal(applyTheme('bogus', root), 'endfield');
  assert.equal(root.dataset.theme, 'endfield');
});

test('ThemeManager populates select, persists changes, and notifies listeners', t => {
  const root = {dataset: {}};
  const created = [];
  globalThis.document = {
    documentElement: root,
    createElement: tag => {
      const node = {tag, value: '', textContent: ''};
      created.push(node);
      return node;
    },
  };
  t.after(() => { delete globalThis.document; });

  const storage = memoryStorage();
  const select = new MockSelect();
  const seen = [];
  const manager = new ThemeManager(select, {storage, root, onChange: id => seen.push(id)});
  assert.equal(created.length, THEMES.length);
  assert.equal(select.value, 'endfield');
  assert.equal(root.dataset.theme, 'endfield');

  select.value = 'loft';
  select.dispatchEvent(new Event('change'));
  assert.equal(manager.current, 'loft');
  assert.equal(root.dataset.theme, 'loft');
  assert.equal(loadTheme(storage), 'loft');
  assert.deepEqual(seen, ['loft']);

  manager.set('invalid');
  assert.equal(manager.current, 'endfield');
  assert.equal(select.value, 'endfield');
});
