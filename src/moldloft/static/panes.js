// Resizable and collapsible workbench sidebars; stores pure UI geometry only.
const STORAGE_KEY = 'moldloft.panes.v1';
export const DEFAULTS = Object.freeze({left: 250, right: 360, leftCollapsed: false, rightCollapsed: false});
export const LIMITS = Object.freeze({leftMin: 180, leftMax: 520, rightMin: 260, rightMax: 680, centerMin: 260});

export function clampPaneWidth(side, width, total, otherWidth = 0) {
  const min = side === 'left' ? LIMITS.leftMin : LIMITS.rightMin;
  const max = side === 'left' ? LIMITS.leftMax : LIMITS.rightMax;
  const available = Number.isFinite(total) && total > 0 ? Math.max(min, total - otherWidth - LIMITS.centerMin) : max;
  const boundedMax = Math.min(max, available);
  return Math.round(Math.max(min, Math.min(boundedMax, Number.isFinite(width) ? width : DEFAULTS[side])));
}

export function loadPaneState(storage = globalThis.localStorage) {
  try {
    const raw = storage?.getItem(STORAGE_KEY);
    if (!raw) return {...DEFAULTS};
    const parsed = JSON.parse(raw);
    return {
      left: Number.isFinite(parsed?.left) ? parsed.left : DEFAULTS.left,
      right: Number.isFinite(parsed?.right) ? parsed.right : DEFAULTS.right,
      leftCollapsed: Boolean(parsed?.leftCollapsed),
      rightCollapsed: Boolean(parsed?.rightCollapsed),
    };
  } catch {
    return {...DEFAULTS};
  }
}

export function savePaneState(state, storage = globalThis.localStorage) {
  try { storage?.setItem(STORAGE_KEY, JSON.stringify(state)); }
  catch {}
}

export class PaneLayout {
  constructor(workspace, {leftSplit, rightSplit, leftToggle, rightToggle, storage = globalThis.localStorage} = {}) {
    this.workspace = workspace;
    this.leftSplit = leftSplit;
    this.rightSplit = rightSplit;
    this.leftToggle = leftToggle;
    this.rightToggle = rightToggle;
    this.storage = storage;
    this.state = loadPaneState(storage);
    this.apply();
    this.bindSplitter(leftSplit, 'left');
    this.bindSplitter(rightSplit, 'right');
    leftToggle?.addEventListener('click', () => this.toggle('left'));
    rightToggle?.addEventListener('click', () => this.toggle('right'));
    globalThis.window?.addEventListener('resize', () => this.apply(false));
    globalThis.window?.addEventListener('keydown', event => {
      if (!event.altKey || event.ctrlKey || event.metaKey || event.isComposing) return;
      if (event.key === '[') { event.preventDefault(); this.toggle('left'); }
      else if (event.key === ']') { event.preventDefault(); this.toggle('right'); }
    });
  }

  apply(persist = false) {
    const total = this.workspace.getBoundingClientRect?.().width || globalThis.window?.innerWidth || 0;
    const otherRight = this.state.rightCollapsed ? 0 : this.state.right;
    const otherLeft = this.state.leftCollapsed ? 0 : this.state.left;
    const left = clampPaneWidth('left', this.state.left, total, otherRight);
    const right = clampPaneWidth('right', this.state.right, total, otherLeft);
    this.workspace.style.setProperty('--left-w', `${left}px`);
    this.workspace.style.setProperty('--right-w', `${right}px`);
    this.workspace.classList.toggle('left-collapsed', this.state.leftCollapsed);
    this.workspace.classList.toggle('right-collapsed', this.state.rightCollapsed);
    this.leftSplit?.setAttribute('aria-valuenow', String(this.state.leftCollapsed ? 0 : left));
    this.rightSplit?.setAttribute('aria-valuenow', String(this.state.rightCollapsed ? 0 : right));
    this.leftToggle?.setAttribute('aria-pressed', String(!this.state.leftCollapsed));
    this.rightToggle?.setAttribute('aria-pressed', String(!this.state.rightCollapsed));
    if (persist) savePaneState({...this.state, left, right}, this.storage);
  }

  toggle(side) {
    const key = side === 'left' ? 'leftCollapsed' : 'rightCollapsed';
    this.state[key] = !this.state[key];
    this.apply(true);
  }

  resize(side, nextWidth) {
    const total = this.workspace.getBoundingClientRect?.().width || globalThis.window?.innerWidth || 0;
    const other = side === 'left' ? (this.state.rightCollapsed ? 0 : this.state.right)
      : (this.state.leftCollapsed ? 0 : this.state.left);
    this.state[side] = clampPaneWidth(side, nextWidth, total, other);
    this.state[side === 'left' ? 'leftCollapsed' : 'rightCollapsed'] = false;
    this.apply(true);
  }

  reset(side) {
    this.state[side] = DEFAULTS[side];
    this.state[side === 'left' ? 'leftCollapsed' : 'rightCollapsed'] = false;
    this.apply(true);
  }

  bindSplitter(handle, side) {
    if (!handle) return;
    let drag = null;
    handle.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      event.preventDefault();
      handle.setPointerCapture?.(event.pointerId);
      drag = {id: event.pointerId, startX: event.clientX, startWidth: this.state[side]};
      globalThis.document?.body?.classList.add('resizing');
      handle.classList.add('dragging');
    });
    handle.addEventListener('pointermove', event => {
      if (!drag || drag.id !== event.pointerId) return;
      const delta = event.clientX - drag.startX;
      this.resize(side, side === 'left' ? drag.startWidth + delta : drag.startWidth - delta);
    });
    const finish = () => {
      if (!drag) return;
      drag = null;
      globalThis.document?.body?.classList.remove('resizing');
      handle.classList.remove('dragging');
    };
    handle.addEventListener('pointerup', finish);
    handle.addEventListener('pointercancel', finish);
    handle.addEventListener('lostpointercapture', finish);
    handle.addEventListener('dblclick', () => this.reset(side));
    handle.addEventListener('keydown', event => {
      if (event.key === 'Home') { event.preventDefault(); this.reset(side); return; }
      const step = event.shiftKey ? 48 : 16;
      if (event.key === 'ArrowLeft') {
        event.preventDefault();
        this.resize(side, this.state[side] + (side === 'left' ? -step : step));
      } else if (event.key === 'ArrowRight') {
        event.preventDefault();
        this.resize(side, this.state[side] + (side === 'left' ? step : -step));
      }
    });
  }
}
