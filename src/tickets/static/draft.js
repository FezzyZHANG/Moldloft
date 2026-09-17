// The editable working copy is separate from server snapshots and AI proposals.
export const fields = ['title', 'description', 'tech_notes', 'status', 'priority', 'group_name', 'parent_id'];
const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
export class TicketDraft {
  constructor(save, changed = () => {}, editable = fields) {
    this.fields = editable;
    this.save = save; this.changed = changed; this.base = null;
    this.values = {}; this.pending = null; this.conflict = null; this.error = null;
  }
  load(ticket) {
    if (this.pending) throw new Error('Cannot replace a saving draft');
    this.base = ticket ? structuredClone(ticket) : null;
    this.values = ticket ? Object.fromEntries(this.fields.map(k => [k, ticket[k]])) : {};
    this.conflict = null; this.error = null; this.changed();
  }
  set(key, value) { this.values[key] = value; this.error = null; this.changed(); }
  get patch() { return Object.fromEntries(this.fields.filter(k => !equal(this.values[k], this.base?.[k])).map(k => [k, this.values[k]])); }
  get dirty() { return this.base !== null && Object.keys(this.patch).length > 0; }
  get state() { return this.conflict ? 'conflict' : this.pending ? 'saving' : this.error ? 'error' : this.dirty ? 'dirty' : 'clean'; }
  observe(ticket) {
    if (!this.base || this.pending) return;
    if (!this.dirty && !this.conflict) { this.load(ticket); return; }
    if (!ticket || ticket.version !== this.base.version) {
      this.conflict = ticket || {deleted: true}; this.changed();
    }
  }
  rebase(ticket) {
    const patch = this.patch;
    this.load(ticket);
    Object.assign(this.values, patch);
    this.changed();
  }
  async flush() {
    if (this.pending) return this.pending;
    if (this.conflict) throw new Error('请先处理版本冲突');
    if (!this.dirty) return;
    const sent = structuredClone(this.values), base = structuredClone(this.base), patch = this.patch;
    this.error = null;
    // Defer save() one microtask so synchronous test adapters follow the same path.
    this.pending = Promise.resolve().then(() => this.save(base, patch)).then(ticket => {
      for (const field of this.fields) if (equal(this.values[field], sent[field])) this.values[field] = ticket[field];
      this.base = structuredClone(ticket);
    }).catch(error => {
      this.error = error;
      if (error.status === 409) this.conflict = error.data?.current || {unknown: true};
      throw error;
    }).finally(() => { this.pending = null; this.changed(); });
    this.changed();
    return this.pending;
  }
}

export class AttentionSaver {
  constructor(draft, isInside, report = () => {}) {
    this.draft = draft; this.isInside = isInside; this.report = report;
    this.composing = false; this.deferred = false; this.running = null;
  }
  async leave() {
    if (this.composing) { this.deferred = true; return; }
    if (this.running) return this.running;
    this.running = (async () => {
      try {
        do { await this.draft.flush(); } while (this.draft.dirty && !this.isInside());
      } catch (error) { this.report(error); throw error; }
      finally { this.running = null; }
    })();
    return this.running;
  }
  compositionEnd() {
    this.composing = false;
    if (this.deferred) { this.deferred = false; return this.leave(); }
    return Promise.resolve();
  }
  async beforeAction(action) {
    if (this.composing) throw new Error('请先完成输入');
    await this.leave();
    if (this.draft.dirty) throw new Error('编辑仍在变化，请稍后重试');
    return action();
  }
}
