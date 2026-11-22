import {AnchorPanel} from './anchors.js';
import {AttentionSaver, ItemDraft} from './draft.js';
import {markdown} from './markdown.js';
import {api, findItem, graphPath, readTarget, targetName} from './store.js';
import {bindForm, button, byId, element, field, noteStates, options, priorities, relationLabels,
  showDialog, statuses} from './ui.js';

const ticketFields = ['title', 'description', 'tech_notes', 'status', 'priority', 'group_name', 'parent_id'];
const noteFields = ['title', 'summary', 'body', 'kind', 'state', 'in_graph', 'links'];
const fieldLabels = {title: '标题', description: '说明', tech_notes: '技术笔记', status: '状态',
  priority: '优先级', group_name: '分组', parent_id: '父工单', summary: '摘要', body: '正文',
  kind: '类型', state: '状态', in_graph: '显示在图谱上', links: '链接'};
const saveLabels = {clean: '已保存', dirty: '有未保存编辑', saving: '保存中…', error: '保存失败', conflict: '版本冲突'};

export class Inspector {
  constructor(store, actions) {
    this.store = store;
    this.actions = actions;
    this.root = byId('inspector');
    this.anchorSection = element('section', {className: 'inspector-section'});
    this.anchors = new AnchorPanel(store, actions, this.anchorSection);
    this.bind();
  }

  bind() {
    const {store, actions, root} = this;
    const move = byId('move-dialog');
    this.moveControls = bindForm(move, async form => {
      const target_graph_id = form.elements.namedItem('target_graph_id').value;
      const id = this.moving;
      const result = await api(`/api/tickets/${id}/move`, 'POST', {target_graph_id});
      return () => actions.run(async () => {
        await actions.openGraph(result.graph_id, {type: 'ticket', id});
        await store.listGraphs();
      });
    });
    const dependency = byId('dependency-dialog');
    this.dependencyControls = bindForm(dependency, async form => {
      const {id, prerequisite} = this.linking;
      const other = Number(form.elements.namedItem('ticket_id').value);
      await store.mutate([this.edge(prerequisite ? other : id, prerequisite ? id : other, 'edge_add')]);
      return () => actions.run(() => store.refresh());
    });

    root.addEventListener('focusout', event => {
      if (root.contains(event.relatedTarget)) return;
      const saver = this.saver;
      setTimeout(() => {
        if (saver && saver === this.saver && !root.contains(document.activeElement)) this.save();
      }, 0);
    });
    document.addEventListener('pointerdown', event => {
      if (!root.contains(event.target)) this.save();
    }, true);
    root.addEventListener('compositionstart', () => this.saver?.compositionStart());
    root.addEventListener('compositionend', () => {
      const saver = this.saver;
      // The final input event can follow compositionend.
      setTimeout(() => {
        saver?.compositionEnd();
        if (saver && saver === this.saver) this.sync();
      }, 0);
    });
    window.addEventListener('blur', () => this.save());
    document.addEventListener('visibilitychange', () => { if (document.hidden) this.save(); });
    document.addEventListener('keydown', event => {
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 's' || !this.draft) return;
      event.preventDefault();
      this.save();
    });
  }

  get unsaved() { return !!(this.draft?.dirty || this.draft?.pending || this.saver?.composing); }

  save() {
    if (!this.unsaved) return;
    void this.saver.leave().catch(() => this.renderSave());
  }

  beforeAction(action) {
    return this.saver ? this.saver.beforeAction(action) : action();
  }

  current(target = this.target) {
    const graph = this.store.graph;
    const row = findItem(graph, target);
    if (!row) return null;
    const item = {...row, revision: graph.revision};
    if (target.type === 'note') item.links = graph.note_links.filter(link => link.source_note_id === row.id)
      .map(({relation, target_type, target_id}) => ({relation, target_type, target_id}));
    return item;
  }

  render(reason) {
    const {selection, graph} = this.store;
    const target = selection?.type === 'proposal' ? null : selection;
    this.root.hidden = selection?.type === 'proposal';
    const key = target ? JSON.stringify([graph.id, target.type, target.id]) : '';
    if (key !== this.key) {
      this.key = key;
      this.target = target;
      this.draft = this.saver = null;
      this.build();
    } else if (this.draft && reason === 'snapshot') {
      this.sync();
    }
    if (target && this.draft && ['open', 'selection', 'snapshot', 'drift'].includes(reason)) {
      this.anchors.render(target);
      this.renderRelations();
    }
  }

  sync() {
    if (this.saver.composing) return;
    const current = this.current();
    if (current && current.revision < this.draft.base?.revision) return;
    this.draft.observe(current);
    if (this.draft.state === 'clean') {
      if (current) this.syncControls();
      else this.build();
    }
    this.renderSave();
  }

  build() {
    const current = this.current();
    this.root.replaceChildren();
    if (!current) {
      this.draft = this.saver = null;
      this.root.append(element('p', {className: 'inspector-empty',
        text: this.target ? '此项目已被移除' : '选择工单、笔记或待审提案'}));
      return;
    }
    const target = this.target;
    const isTicket = target.type === 'ticket';
    const graphId = this.store.graph.id;
    this.controls = new Map();
    this.draft = new ItemDraft(async (base, fields) => {
      const path = `${graphPath(graphId)}/${target.type}s/${encodeURIComponent(target.id)}`;
      const payload = {version: base.version, fields};
      if (isTicket && ('group_name' in fields || 'parent_id' in fields)) payload.revision = base.revision;
      try {
        const result = await api(path, 'PATCH', payload);
        await this.store.refresh(graphId).catch(this.actions.report);
        return {...base, ...fields, revision: result.revision,
          version: result.versions[`${target.type}:${target.id}`] ?? base.version};
      } catch (error) {
        if (error.status === 409 || error.status === 404) {
          setTimeout(() => this.store.refresh(graphId).catch(this.actions.report), 0);
        }
        throw error;
      }
    }, () => {
      if (!this.draft) return;
      this.renderSave();
      if (this.wasSaving && !this.draft.pending && !this.draft.error) {
        const draft = this.draft;
        queueMicrotask(() => { if (this.draft === draft) this.sync(); });
      }
      this.wasSaving = !!this.draft.pending;
    }, isTicket ? ticketFields : noteFields);
    this.saver = new AttentionSaver(this.draft, () => this.root.contains(document.activeElement));
    this.saveStatus = element('span', {className: 'save-status', role: 'status'});
    this.conflict = element('div', {className: 'conflict-box', hidden: ''});
    const title = element('div', {className: 'inspector-heading'},
      element('div', {}, element('p', {className: 'item-id', text: isTicket ? '工单' : '笔记'}),
        element('h2', {text: targetName(target.type, target.id)})), this.saveStatus);
    const fields = element('div', {className: 'editor-fields'});
    fields.append(this.text('title', 200));
    if (isTicket) {
      fields.append(element('div', {className: 'field-grid'},
        this.select('status', statuses), this.select('priority', priorities)),
      element('div', {className: 'field-grid'},
        this.text('group_name', 80), this.select('parent_id', [], select => select.value ? Number(select.value) : null)),
      this.markdownField('description'), this.markdownField('tech_notes'));
    } else {
      const kind = this.select('kind', Object.keys(noteStates).map(value => [value, value]), select => {
        this.draft.set('state', noteStates[select.value][0]);
        this.syncNoteStates(select.value);
        return select.value;
      });
      fields.append(element('div', {className: 'field-grid'}, kind, this.select('state', [])),
        this.control('in_graph', element('input', {type: 'checkbox'}), node => node.checked, true),
        this.text('summary', 1000, true), this.markdownField('body'));
    }
    this.quick = element('div', {className: 'toolbar inspector-actions'});
    this.relations = element('section', {className: 'inspector-section'});
    this.root.append(title, this.conflict, fields, this.quick, this.relations, this.anchorSection);
    this.draft.load(current);
    this.syncControls();
  }

  control(key, node, read = control => control.value, toggle = false) {
    this.controls.set(key, {node, read});
    node.addEventListener(node.matches('select, [type=checkbox]') ? 'change' : 'input', () => {
      this.draft.set(key, read(node));
      if (key === 'kind') this.renderRelations();
      if (key === 'parent_id') this.updateGroupControl();
    });
    if (toggle) return element('label', {className: 'toggle'}, node, fieldLabels[key]);
    return field(fieldLabels[key], node);
  }

  text(key, maxlength, multiline = false) {
    return this.control(key, element(multiline ? 'textarea' : 'input', {maxlength, rows: multiline ? 3 : null}));
  }

  select(key, values, read) {
    return this.control(key, options(element('select'), values), read);
  }

  markdownField(key) {
    const editor = element('textarea', {rows: 5, maxlength: 20000, 'aria-label': fieldLabels[key]});
    this.control(key, editor);
    const preview = element('div', {hidden: ''});
    const edit = button('编辑', () => toggle(false), {'aria-pressed': 'true'});
    const read = button('预览', () => toggle(true), {'aria-pressed': 'false'});
    const update = () => {
      if (!preview.hidden) preview.replaceChildren(markdown(editor.value, ref => this.actions.reference(ref)));
    };
    function toggle(show) {
      preview.hidden = !show;
      editor.hidden = show;
      read.setAttribute('aria-pressed', show);
      edit.setAttribute('aria-pressed', !show);
      update();
    }
    this.controls.get(key).update = update;
    return element('div', {className: 'markdown-field'},
      element('div', {className: 'section-heading'}, element('span', {text: fieldLabels[key]}),
        element('div', {className: 'segmented'}, edit, read)), editor, preview);
  }

  syncNoteStates(kind) {
    const states = noteStates[kind];
    options(this.controls.get('state').node, states.map(state => [state, state]), this.draft.values.state);
  }

  updateGroupControl() {
    const {parent_id, group_name} = this.draft.values;
    const group = this.controls.get('group_name').node;
    group.disabled = parent_id !== null;
    group.value = this.store.graph.tickets.find(row => row.id === parent_id)?.group_name ?? group_name;
  }

  syncControls() {
    const {values} = this.draft;
    const {graph} = this.store;
    if (this.target.type === 'ticket') {
      const excluded = new Set([this.target.id]);
      const children = new Map();
      for (const row of graph.tickets) {
        if (!children.has(row.parent_id)) children.set(row.parent_id, []);
        children.get(row.parent_id).push(row.id);
      }
      for (const id of excluded) for (const child of children.get(id) || []) excluded.add(child);
      options(this.controls.get('parent_id').node, [['', '无父工单'], ...graph.tickets
        .filter(row => !excluded.has(row.id)).map(row => [row.id, `#${row.id} ${row.title}`])]);
    } else this.syncNoteStates(values.kind);
    for (const [key, {node, update}] of this.controls) {
      if (node.type === 'checkbox') node.checked = values[key];
      else if (node.value !== String(values[key] ?? '')) node.value = values[key] ?? '';
      update?.();
    }
    if (this.target.type === 'ticket') this.updateGroupControl();
    this.renderQuick();
    this.renderRelations();
  }

  renderSave() {
    const draft = this.draft;
    if (!draft || !this.saveStatus) return;
    this.saveStatus.textContent = saveLabels[draft.state];
    this.saveStatus.dataset.state = draft.state;
    const box = this.conflict;
    box.hidden = !draft.conflict && !draft.error;
    if (box.hidden) return;
    const current = this.current();
    const conflict = draft.conflict;
    box.replaceChildren(element('strong', {text: conflict ? '版本冲突 · 本地草稿已保留' : '保存失败 · 本地草稿已保留'}));
    if (draft.error) box.append(element('p', {text: draft.error.message}));
    if (!conflict) {
      box.append(button('重试保存', () => this.save()));
      return;
    }
    const usable = current && !conflict.deleted && !(current.version < conflict.current?.version) &&
      !(current.revision < conflict.revision) && (current.version !== draft.base.version ||
        current.revision !== draft.base.revision);
    if (usable) {
      const describe = value => typeof value === 'string' ? value : JSON.stringify(value ?? null);
      for (const [key, value] of Object.entries(draft.patch)) {
        box.append(element('details', {}, element('summary', {text: fieldLabels[key]}),
          element('pre', {text: `服务器\n${describe(current[key])}\n\n本地草稿\n${describe(value)}`})));
      }
      box.append(element('div', {className: 'toolbar'}, button('采用服务器版本', () => {
        draft.load(current);
        this.syncControls();
      }), button('将草稿合入最新版本', () => {
        draft.rebase(current);
        this.syncControls();
        this.save();
      })));
    } else if (conflict.deleted) {
      box.append(element('p', {text: '此项目已被移除。可以从编辑器复制草稿内容。'}),
        button('放弃草稿', () => { draft.load(null); this.build(); }));
    } else box.append(button('重新读取服务器版本', () => this.actions.run(() => this.store.refresh())));
  }

  edge(blocker_id, blocked_id, op) { return {op, blocker_id, blocked_id}; }

  runAfterSave(action) {
    return () => this.actions.run(() => this.actions.guard(action));
  }

  renderQuick() {
    const {store, target} = this;
    const isTicket = target.type === 'ticket';
    const row = this.current();
    const {id, type} = target;
    const actions = [];
    if (isTicket) {
      actions.push(button('标为完成', () => { this.draft.set('status', 'done'); this.controls.get('status').node.value = 'done'; this.save(); },
        {disabled: row.status === 'done' ? '' : null}),
      button('连接后续工单', this.runAfterSave(() => this.actions.connect(id))),
      button('添加前置', this.runAfterSave(() => this.pickDependency(id, true))),
      button('添加后续', this.runAfterSave(() => this.pickDependency(id, false))),
      button('移动到图谱', this.runAfterSave(async () => {
        this.moving = id;
        const graphs = await store.listGraphs();
        const choices = graphs.filter(row => row.id !== store.graph.id).map(row => [row.id, row.name]);
        if (!choices.length) throw new Error('没有其他图谱可供移动');
        options(this.moveControls.namedItem('target_graph_id'), choices);
        showDialog(byId('move-dialog'));
      })));
    } else if (row.adoption_stale) {
      actions.push(button('重新采纳前提', this.runAfterSave(async () => {
        const item = this.current();
        if (item.kind !== 'assumption' || item.state !== 'adopted') throw new Error('当前笔记不是已采纳的前提');
        await api(`${graphPath(item.graph_id)}/notes/${id}`, 'PATCH',
          {fields: {state: 'adopted'}, version: item.version});
        await store.refresh();
      })));
    }
    actions.push(button('删除', this.runAfterSave(async () => {
      if (!confirm(`删除「${this.current().title}」？`)) return;
      const {graph} = store;
      const query = isTicket ? `?revision=${graph.revision}` : '';
      await api(`${graphPath(graph.id)}/${type}s/${id}${query}`, 'DELETE');
      store.select(null);
      await store.refresh();
    }), {className: 'danger'}));
    this.quick.replaceChildren(...actions);
  }

  pickDependency(id, prerequisite) {
    const graph = this.store.graph;
    this.linking = {id, prerequisite};
    byId('dependency-dialog-title').textContent = prerequisite ? '添加前置工单' : '添加后续工单';
    const existing = new Set(graph.edges.filter(edge => prerequisite ? edge.blocked_id === id : edge.blocker_id === id)
      .map(edge => prerequisite ? edge.blocker_id : edge.blocked_id));
    const choices = graph.tickets.filter(row => row.id !== id && !existing.has(row.id))
      .map(row => [row.id, `#${row.id} ${row.title}`]);
    if (!choices.length) throw new Error('没有其他可连接的工单');
    options(this.dependencyControls.namedItem('ticket_id'), choices);
    showDialog(byId('dependency-dialog'));
  }

  renderRelations() {
    if (!this.draft) return;
    const {target, store, relations} = this;
    const {graph} = store;
    const targetButton = (type, id) => {
      const item = findItem(graph, {type, id});
      return button(item ? `${targetName(type, id)} ${item.title}` : targetName(type, id),
        () => this.actions.reference(targetName(type, id)), {className: 'text-link'});
    };
    const row = (label, link, remove) => element('li', {className: 'relation-row'},
      element('span', {className: 'muted', text: label}), link,
      ...(remove ? [button('移除', remove, {className: 'danger'})] : []));
    const rows = [];
    if (target.type === 'ticket') {
      for (const edge of graph.edges) {
        const {blocker_id: blocker, blocked_id: blocked} = edge;
        if (blocker !== target.id && blocked !== target.id) continue;
        const upstream = blocked === target.id;
        rows.push(row(upstream ? '前置' : '后续', targetButton('ticket', upstream ? blocker : blocked),
          this.runAfterSave(async () => {
            await store.mutate([this.edge(blocker, blocked, 'edge_remove')]);
            await store.refresh();
          })));
      }
      const item = this.current();
      for (const note of item?.review_notes || []) rows.push(row('前提待复核', targetButton('note', note)));
    } else {
      const {links, kind} = this.draft.values;
      for (const [index, link] of links.entries()) {
        rows.push(row(relationLabels[link.relation], targetButton(link.target_type, link.target_id), () => {
          this.draft.set('links', links.filter((_, i) => i !== index));
          this.renderRelations();
        }));
      }
      const allowed = kind === 'assumption' ? ['assumes', 'relates'] :
        kind === 'evidence' ? ['supports', 'challenges', 'relates'] : ['relates'];
      const relation = options(element('select', {'aria-label': '链接类型'}),
        allowed.map(key => [key, relationLabels[key]]));
      const choices = [
        ...graph.tickets.map(item => [String(item.id), `#${item.id} ${item.title}`]),
        ...graph.notes.filter(item => item.id !== target.id).map(item => [item.id, `${item.id} ${item.title}`]),
      ];
      const other = options(element('select', {'aria-label': '链接目标'}), choices);
      const add = button('添加链接', () => {
        const {type, id} = readTarget(other.value);
        const link = {relation: relation.value, target_type: type, target_id: id};
        if (this.draft.values.links.some(existing => existing.relation === link.relation &&
            existing.target_type === type && existing.target_id === id)) return;
        this.draft.set('links', [...this.draft.values.links, link]);
        this.renderRelations();
      }, {disabled: choices.length ? null : ''});
      rows.push(element('li', {className: 'link-editor'}, relation, other, add));
    }
    const backlinks = graph.note_links.filter(link => link.target_type === target.type && link.target_id === target.id)
      .map(link => row(relationLabels[link.relation], targetButton('note', link.source_note_id)));
    relations.replaceChildren(element('h3', {text: target.type === 'ticket' ? '依赖与前提' : '链接'}),
      ...(rows.length ? [element('ul', {className: 'relations'}, ...rows)] :
        [element('p', {className: 'empty', text: '暂无连接'})]),
      element('h3', {text: '反向链接'}),
      ...(backlinks.length ? [element('ul', {className: 'relations'}, ...backlinks)] :
        [element('p', {className: 'empty', text: '暂无反向链接'})]));
  }
}
