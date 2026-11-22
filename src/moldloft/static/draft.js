// A draft owns its editable fields and the version they were based on.
const copy = value => structuredClone(value);
const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);

export class ItemDraft {
  constructor(save, changed = () => {}, editable = []) {
    this.save = save;
    this.changed = changed;
    this.editable = editable;
    this.load(null);
  }

  load(item) {
    if (this.pending) throw new Error('Cannot replace a saving draft');
    this.base = copy(item);
    this.values = item ? Object.fromEntries(this.editable.map(key => [key, copy(item[key])])) : {};
    this.conflict = null;
    this.error = null;
    this.changed();
  }

  set(key, value) {
    if (!this.editable.includes(key)) throw new Error(`Unknown draft field: ${key}`);
    this.values[key] = copy(value);
    this.error = null;
    this.changed();
  }

  get patch() {
    return Object.fromEntries(this.editable.filter(key => !equal(this.base?.[key], this.values[key]))
      .map(key => [key, this.values[key]]));
  }

  get dirty() { return !!this.base && Object.keys(this.patch).length > 0; }

  get state() {
    if (this.conflict) return 'conflict';
    if (this.pending) return 'saving';
    if (this.error) return 'error';
    return this.dirty ? 'dirty' : 'clean';
  }

  observe(item) {
    if (!this.base || this.pending) return;
    if (item && (item.version < this.conflict?.current?.version || item.revision < this.conflict?.revision)) return;
    if (!this.dirty && !this.conflict) {
      if (!equal(item, this.base)) this.load(item);
    } else if (!item || item.version !== this.base.version ||
        (this.conflict && item.revision !== this.base.revision)) {
      const conflict = item ? {current: copy(item)} : {deleted: true};
      if (!equal(this.conflict, conflict)) {
        this.conflict = conflict;
        this.changed();
      }
    }
  }

  rebase(item) {
    if (!item) throw new Error('No current item to rebase onto');
    const patch = this.patch;
    this.load(item);
    Object.assign(this.values, patch);
    this.changed();
  }

  flush() {
    if (this.pending) return this.pending;
    if (this.conflict) return Promise.reject(new Error('请先处理版本冲突'));
    if (!this.dirty) return Promise.resolve();
    const sent = copy(this.values);
    const base = copy(this.base);
    const patch = copy(this.patch);
    this.error = null;
    this.pending = Promise.resolve().then(() => this.save(base, patch)).then(item => {
      for (const key of this.editable) {
        if (equal(this.values[key], sent[key])) this.values[key] = copy(item[key]);
      }
      this.base = copy(item);
    }).catch(error => {
      this.error = error;
      if (error.status === 409 && Object.hasOwn(error.data || {}, 'current')) {
        const current = error.data?.current;
        this.conflict = current === null ? {deleted: true} :
          typeof current === 'object' ? {current: copy(current)} : {revision: current};
      }
      throw error;
    }).finally(() => {
      this.pending = null;
      this.changed();
    });
    this.changed();
    return this.pending;
  }
}

export class AttentionSaver {
  constructor(draft, isInside) {
    this.draft = draft;
    this.isInside = isInside;
    this.composing = false;
  }

  compositionStart() {
    this.composing = true;
  }

  compositionEnd() {
    this.composing = false;
    this.resume?.();
  }

  leave() {
    if (this.running) return this.running;
    this.running = Promise.resolve().then(async () => {
      do {
        while (this.composing) {
          await new Promise(resolve => { this.resume = resolve; });
          this.resume = null;
        }
        await this.draft.flush();
      } while (this.draft.dirty && !this.isInside());
    }).finally(() => { this.running = null; });
    return this.running;
  }

  async beforeAction(action) {
    await this.leave();
    if (this.draft.dirty) throw new Error('编辑仍在变化，请稍后重试');
    return action();
  }
}
