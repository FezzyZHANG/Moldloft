import {TicketDraft, AttentionSaver} from './draft.js';
import {abstractFields, noteFields, noteKinds, noteStates, relationNames, relationTypes} from './notes-data.js';
const $ = id => document.getElementById(id);

export class NotesEditor {
  constructor({graph, api, owns, saved, report, open}) {
    Object.assign(this, {graph, api, owns, saved, report, open});
    this.active = false; this.isNew = false; this.creating = false;
    this.form = $('note-form');
    this.abstractWidth = 0;
    new ResizeObserver(entries => {
      const width = entries[0].contentRect.width;
      if (width !== this.abstractWidth) { this.abstractWidth = width; this.resizeAbstracts(); }
    }).observe(this.form);
    this.draft = new TicketDraft(async (base, fields) => {
      if (this.isNew) throw new Error('请先创建笔记');
      const result = await this.write(base.id, fields, base.version);
      await this.saved(); return result;
    }, () => this.renderState(), noteFields);
    this.attention = new AttentionSaver(this.draft, () => owns(document.activeElement), report);
    this.form.elements.kind.replaceChildren(...Object.entries(noteKinds).map(([id, title]) => new Option(title, id)));
    this.form.addEventListener('input', event => {
      const name = event.target.name;
      if (!noteFields.includes(name)) return;
      if (name === 'kind') {
        const allowed = relationTypes(event.target.value);
        if (this.draft.values.relations.some(r => !allowed.includes(r.type))) {
          event.target.value = this.draft.values.kind;
          report(new Error('请先移除不适用于新类型的关系')); return;
        }
        this.draft.set(name, event.target.value);
        this.draft.set('state', Object.keys(noteStates[event.target.value])[0]);
        this.renderOptions();
      } else this.draft.set(name, event.target.value);
    });
    this.form.onsubmit = event => { event.preventDefault(); this.saveNow().catch(report); };
    $('note-add-relation').onclick = () => {
      const type = $('note-relation-type').value, target = $('note-relation-target').value;
      if (!target) { report(new Error('请选择关系目标')); return; }
      if (this.draft.values.relations.some(r => r.type === type && r.target === target)) return;
      this.draft.set('relations', [...this.draft.values.relations, {type, target}]); this.renderRelations();
    };
    $('note-relation-type').onchange = () => this.renderRelationHelp();
    $('note-use-server').onclick = async () => {
      try {
        await saved();
        const current = graph().notes.find(n => n.id === this.id);
        if (current) this.show(current); else { this.hide(); open(null); }
      } catch (error) { report(error); }
    };
    $('note-keep-draft').onclick = async () => {
      try {
        await saved(); const current = graph().notes.find(n => n.id === this.id);
        if (!current) throw new Error('笔记已被移除，请复制草稿后新建');
        this.draft.rebase(current); this.render(); await this.draft.flush();
      } catch (error) { report(error); }
    };
    $('note-cancel').onclick = () => { this.hide(); open(null); };
  }
  get id() { return this.active ? this.draft.base?.id : null; }
  write(id, fields, version) {
    const body = JSON.stringify({fields, version});
    return this.api(`/api/notes/${id}`, {method: 'PUT', body, keepalive: new Blob([body]).size < 60000});
  }
  show(note) { this.active = true; this.isNew = false; this.draft.load(note); this.render(); }
  start(target) {
    this.active = true; this.isNew = true;
    this.draft.load({id: `n-${crypto.randomUUID()}`, title: '', kind: 'question', state: 'open', body: '', abstract_zh: '', abstract_en: '',
      relations: target ? [{type: 'relates', target}] : [], version: null});
    this.render(); this.form.elements.title.focus();
  }
  hide() { this.active = false; this.isNew = false; this.draft.load(null); this.form.hidden = true; }
  observe() {
    if (!this.active || this.isNew || this.draft.pending || this.creating) return;
    const current = this.graph().notes.find(n => n.id === this.id);
    // Refreshing another record must not reset a relation picker mid-selection.
    if (current?.version === this.draft.base?.version && !this.draft.conflict) return;
    const edited = this.draft.dirty || this.draft.conflict;
    this.draft.observe(current || null);
    if (!edited) { if (current) this.render(); else this.hide(); }
  }
  async beforeAction() {
    if (this.creating) throw new Error('正在创建笔记，请等待确认');
    if (this.isNew && this.draft.dirty) throw new Error('新笔记尚未创建，请先创建或取消');
    if (!this.isNew) await this.attention.beforeAction(() => {});
  }
  async saveNow() {
    if (this.attention.composing || this.creating) return;
    if (!this.isNew) { await this.draft.flush(); return; }
    this.creating = true;
    for (const control of this.form.elements) control.disabled = true;
    try {
      const note = await this.write(this.id, this.draft.values, null);
      this.isNew = false; this.draft.load(note); await this.saved(); this.render();
    } finally {
      this.creating = false;
      for (const control of this.form.elements) control.disabled = false;
    }
  }
  renderState() {
    if (!this.active) return;
    const state = this.draft.state;
    $('save-state').textContent = this.isNew ? '尚未创建' : {clean: '已保存', dirty: '编辑中 · 离开时保存', saving: '保存中…', conflict: '版本冲突', error: '保存未完成'}[state];
    $('save-state').dataset.state = state;
    $('note-conflict').hidden = !this.draft.conflict;
    if (this.draft.conflict) {
      const current = this.draft.conflict;
      $('note-conflict-current').textContent = current.deleted ? '磁盘文件已被移除。你的草稿仍保留。' : noteFields.map(f => `${f}: ${typeof current[f] === 'object' ? JSON.stringify(current[f]) : current[f] ?? ''}`).join('\n');
      $('note-keep-draft').disabled = !!current.deleted;
    }
    if (state === 'clean' && this.draft.base) {
      for (const name of ['title', ...abstractFields, 'body']) this.form.elements[name].value = this.draft.values[name];
      this.form.elements.state.value = this.draft.values.state;
    }
    $('note-adoption').hidden = !this.draft.base?.adoption_stale;
    const missing = abstractFields.filter(name => !this.draft.values[name]?.trim());
    $('note-abstract-hint').textContent = missing.length ? `待补充${missing.map(name => name === 'abstract_zh' ? '中文摘要' : 'English abstract').join('、')}；可以先保存灵感，稍后补齐。` :
      '保留核心想法、不确定之处与探索条件。正文或判断改变时，请同步核对两份摘要。';
    this.resizeAbstracts();
  }
  resizeAbstracts() {
    if (!this.active || this.form.hidden) return;
    for (const name of abstractFields) {
      const control = this.form.elements[name];
      control.style.height = 'auto';
      control.style.height = `${Math.min(320, Math.max(58, control.scrollHeight + 2))}px`;
    }
  }
  renderOptions() {
    const {kind, state} = this.draft.values;
    const selectedType = $('note-relation-type').value, selectedTarget = $('note-relation-target').value;
    this.form.elements.state.replaceChildren(...Object.entries(noteStates[kind]).map(([id, title]) => new Option(title, id)));
    this.form.elements.state.value = state;
    $('note-relation-type').replaceChildren(...relationTypes(kind).map(type => new Option(relationNames[type], type)));
    if (relationTypes(kind).includes(selectedType)) $('note-relation-type').value = selectedType;
    this.renderRelationHelp();
    const graph = this.graph();
    $('note-relation-target').replaceChildren(new Option('选择工单或笔记', ''),
      ...graph.tickets.map(t => new Option(`#${t.id} ${t.title.slice(0, 40)}`, `ticket:${t.id}`)),
      ...(graph.notes || []).filter(n => n.id !== this.id).map(n => new Option(`◇ ${n.title}`, `note:${n.id}`)));
    if ([...$('note-relation-target').options].some(o => o.value === selectedTarget)) $('note-relation-target').value = selectedTarget;
  }
  renderRelations() {
    $('note-relations').replaceChildren();
    for (const [index, relation] of this.draft.values.relations.entries()) {
      const row = document.createElement('div'); row.className = 'relation';
      const label = document.createElement('span'); label.className = 'label';
      const graph = this.graph();
      const target = relation.target.startsWith('ticket:') ? graph.tickets.find(t => t.id === Number(relation.target.slice(7))) : graph.notes.find(n => n.id === relation.target.slice(5));
      label.textContent = `${relationNames[relation.type]} ${relation.type === 'relates' ? '—' : '→'} ${target?.title || relation.target + '（目标缺失）'}`;
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'ghost'; remove.textContent = '移除';
      remove.onclick = () => { this.draft.set('relations', this.draft.values.relations.filter((_, i) => i !== index)); this.renderRelations(); };
      row.append(label, remove); $('note-relations').append(row);
    }
  }
  renderRelationHelp() {
    $('note-relation-help').textContent = {relates: '上下文关联，不要求先后顺序。', assumes: '箭头指向采用此假设的工单或笔记；前提变化时提示复核。',
      supports: '此证据支持目标判断；关联本身不代表判断已成立。', option: '此想法是目标的候选方案，不会成为执行阻塞。'}[$('note-relation-type').value];
  }
  render() {
    this.form.hidden = !this.active;
    if (!this.active) return;
    $('editor-heading').textContent = this.isNew ? '新建笔记' : noteKinds[this.draft.values.kind];
    $('select-hint').hidden = true; $('ticket-form').hidden = true; $('relationships').hidden = true; $('code-links').hidden = true;
    $('note-path').textContent = `notes/${this.id}.md`;
    this.form.elements.kind.value = this.draft.values.kind;
    this.renderOptions();
    for (const name of ['title', ...abstractFields, 'body']) this.form.elements[name].value = this.draft.values[name];
    $('note-save').textContent = this.isNew ? '创建笔记' : '保存'; $('note-cancel').hidden = !this.isNew;
    this.renderRelations(); this.renderState();
  }
}
