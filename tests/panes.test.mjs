import assert from 'node:assert/strict';
import test from 'node:test';
import {clampPaneWidth, DEFAULTS, loadPaneState, PaneLayout, savePaneState} from '../src/moldloft/static/panes.js';

class MockClassList {
  constructor() { this.set = new Set(); }
  add(name) { this.set.add(name); }
  remove(name) { this.set.delete(name); }
  toggle(name, force) {
    const next = force === undefined ? !this.set.has(name) : Boolean(force);
    if (next) this.set.add(name); else this.set.delete(name);
    return next;
  }
  contains(name) { return this.set.has(name); }
}

class MockElement extends EventTarget {
  constructor(width = 0) {
    super();
    this.width = width;
    this.attrs = new Map();
    this.props = new Map();
    this.classList = new MockClassList();
    this.style = {
      setProperty: (k, v) => this.props.set(k, v),
      getPropertyValue: k => this.props.get(k) || '',
    };
  }
  getBoundingClientRect() { return {width: this.width}; }
  setAttribute(k, v) { this.attrs.set(k, String(v)); }
  getAttribute(k) { return this.attrs.get(k); }
  setPointerCapture() {}
}

class MockEvent extends Event {
  constructor(type, init = {}) {
    super(type, {cancelable: true});
    Object.assign(this, init);
  }
}

function memoryStorage() {
  const data = new Map();
  return {
    getItem: k => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => data.set(k, String(v)),
  };
}

test('clampPaneWidth enforces min/max limits and preserves center canvas space', () => {
  assert.equal(clampPaneWidth('left', 100, 1400, 360), 180);
  assert.equal(clampPaneWidth('left', 700, 1400, 360), 520);
  assert.equal(clampPaneWidth('right', 120, 1400, 250), 260);
  assert.equal(clampPaneWidth('right', 800, 1400, 250), 680);
  // 900 total - 360 other - 260 centerMin = 280 max for left
  assert.equal(clampPaneWidth('left', 400, 900, 360), 280);
});

test('loadPaneState and savePaneState handle defaults, persistence, and corrupt JSON', () => {
  const storage = memoryStorage();
  assert.deepEqual(loadPaneState(storage), DEFAULTS);
  savePaneState({left: 310, right: 420, leftCollapsed: true, rightCollapsed: false}, storage);
  assert.deepEqual(loadPaneState(storage), {left: 310, right: 420, leftCollapsed: true, rightCollapsed: false});
  storage.setItem('moldloft.panes.v1', '{bad json');
  assert.deepEqual(loadPaneState(storage), DEFAULTS);
});

test('PaneLayout supports pointer dragging, dblclick reset, toggles, and keyboard resizing', t => {
  const win = new EventTarget();
  win.innerWidth = 1400;
  const body = new MockElement();
  globalThis.window = win;
  globalThis.document = {body};
  t.after(() => { delete globalThis.window; delete globalThis.document; });

  const storage = memoryStorage();
  const workspace = new MockElement(1400);
  const leftSplit = new MockElement();
  const rightSplit = new MockElement();
  const leftToggle = new MockElement();
  const rightToggle = new MockElement();

  const layout = new PaneLayout(workspace, {leftSplit, rightSplit, leftToggle, rightToggle, storage});
  assert.equal(workspace.style.getPropertyValue('--left-w'), '250px');
  assert.equal(workspace.style.getPropertyValue('--right-w'), '360px');

  // Drag left splitter right by 80px -> 330px
  leftSplit.dispatchEvent(new MockEvent('pointerdown', {button: 0, pointerId: 1, clientX: 250}));
  assert.equal(body.classList.contains('resizing'), true);
  leftSplit.dispatchEvent(new MockEvent('pointermove', {pointerId: 1, clientX: 330}));
  leftSplit.dispatchEvent(new MockEvent('pointerup', {pointerId: 1}));
  assert.equal(body.classList.contains('resizing'), false);
  assert.equal(workspace.style.getPropertyValue('--left-w'), '330px');
  assert.equal(leftSplit.getAttribute('aria-valuenow'), '330');

  // Drag right splitter left by 60px -> width increases by 60px to 420px
  rightSplit.dispatchEvent(new MockEvent('pointerdown', {button: 0, pointerId: 2, clientX: 1000}));
  rightSplit.dispatchEvent(new MockEvent('pointermove', {pointerId: 2, clientX: 940}));
  rightSplit.dispatchEvent(new MockEvent('pointerup', {pointerId: 2}));
  assert.equal(workspace.style.getPropertyValue('--right-w'), '420px');

  // Keyboard resize on left splitter
  leftSplit.dispatchEvent(new MockEvent('keydown', {key: 'ArrowRight', shiftKey: true}));
  assert.equal(workspace.style.getPropertyValue('--left-w'), '378px');
  leftSplit.dispatchEvent(new MockEvent('keydown', {key: 'Home'}));
  assert.equal(workspace.style.getPropertyValue('--left-w'), '250px');

  // Double-click right splitter resets to 360px
  rightSplit.dispatchEvent(new MockEvent('dblclick'));
  assert.equal(workspace.style.getPropertyValue('--right-w'), '360px');

  // Toggle buttons & Alt+[ / Alt+] shortcuts
  leftToggle.dispatchEvent(new MockEvent('click'));
  assert.equal(workspace.classList.contains('left-collapsed'), true);
  assert.equal(leftToggle.getAttribute('aria-pressed'), 'false');
  assert.equal(leftSplit.getAttribute('aria-valuenow'), '0');

  win.dispatchEvent(new MockEvent('keydown', {key: '[', altKey: true}));
  assert.equal(workspace.classList.contains('left-collapsed'), false);
  assert.equal(leftToggle.getAttribute('aria-pressed'), 'true');

  win.dispatchEvent(new MockEvent('keydown', {key: ']', altKey: true}));
  assert.equal(workspace.classList.contains('right-collapsed'), true);
  assert.equal(rightToggle.getAttribute('aria-pressed'), 'false');
});
