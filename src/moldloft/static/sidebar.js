import {filterItems, groupKey, groupValue} from './layout.js';
import {api, graphPath, targetName} from './store.js';
import {bindForm, button, byId, element, noteStates, options, showDialog, statuses} from './ui.js';

const empty = text => element('p', {className: 'empty', text});
const statusText = new Map(statuses);

export class Sidebar {
  constructor(store, actions) {
    this.store = store;
    this.actions = actions;
    this.graphSelect = byId('graph-select');
    this.status = byId('status-filter');
    this.group = byId('group-filter');
    this.search = byId('search');
    this.bind();
  }

  bind() {
    const {store, actions} = this;
    const run = actions.run;
    const graphDialog = byId('graph-dialog');
    const graphControls = bindForm(graphDialog, async form => {
      const name = form.elements.namedItem('name').value.trim();
      const original = this.renaming;
      const graph = original ? await api(graphPath(original), 'PATCH', {name})
        : await api('/api/graphs', 'POST', {name});
      return () => run(async () => {
        await store.listGraphs();
        if (original) await store.refresh(original);
        else await actions.openGraph(graph.id);
      });
    });
    const open = renaming => run(() => actions.guard(() => {
      this.renaming = renaming ? store.graph.id : null;
      byId('graph-dialog-title').textContent = renaming ? '重命名图谱' : '新建图谱';
      graphControls.namedItem('name').value = renaming ? store.graph.name : '';
      showDialog(graphDialog);
    }));
    byId('new-graph').addEventListener('click', () => open(false));
    byId('rename-graph').addEventListener('click', () => open(true));
    byId('delete-graph').addEventListener('click', () => run(() => actions.guard(async () => {
      const {id, name} = store.graph;
      if (!confirm(`删除图谱「${name}」及其内容？`)) return;
      await api(graphPath(id), 'DELETE');
      await actions.openGraph('default');
      await store.listGraphs();
    })));

    const itemDialog = byId('item-dialog');
    this.itemControls = bindForm(itemDialog, async form => {
      const graph = store.graph;
      if (graph.id !== this.creatingIn) throw new Error('图谱已切换');
      const read = name => form.elements.namedItem(name);
      const fields = {title: read('title').value.trim()};
      const type = this.newType;
      let id;
      if (type === 'ticket') {
        const result = await api(`${graphPath(graph.id)}/tickets`, 'POST', {fields});
        id = result.created['new:ticket'];
      } else {
        Object.assign(fields, {kind: read('kind').value, in_graph: read('in_graph').checked});
        const suffix = () => [...crypto.getRandomValues(new Uint8Array(10))]
          .map(byte => byte.toString(16).padStart(2, '0')).join('');
        id = read('id').value || `n-${suffix()}`;
        await api(`${graphPath(graph.id)}/notes`, 'POST', {id, fields});
      }
      return () => run(async () => {
        await store.refresh(graph.id);
        await actions.select({type, id}, {reveal: true});
      });
    });
    options(this.itemControls.namedItem('kind'), Object.keys(noteStates).map(kind => [kind, kind]));

    this.graphSelect.addEventListener('change', () => {
      const id = this.graphSelect.value;
      this.graphSelect.value = store.graph?.id || '';
      run(() => actions.openGraph(id));
    });
    options(this.status, [['', '全部状态'], ['active', '未完成 (待处理 + 进行中)'], ['ready', '可立即执行 (Ready)'], ...statuses]);
    this.status.addEventListener('change', () => store.updateView({status: this.status.value}));
    this.group.addEventListener('change', () => store.updateView({group: this.group.value}));
    this.search.addEventListener('input', () => store.updateView({search: this.search.value}));
    byId('graph-mode').addEventListener('click', () => store.updateView({mode: 'graph'}));
    byId('list-mode').addEventListener('click', () => store.updateView({mode: 'list'}));
    byId('pending-button').addEventListener('click', () => {
      byId('pending-section').focus({preventScroll: true});
      byId('pending-section').scrollIntoView({block: 'nearest'});
    });
    byId('clear-proposals').addEventListener('click', () => run(() => actions.guard(async () => {
      if (!confirm('清除当前图谱中已应用、已拒绝和已过期的提案？')) return;
      const {graph, inspection} = store;
      await api(`${graphPath(graph.id)}/proposals/clear`, 'POST', {});
      if (inspection && inspection.status !== 'pending') await actions.select(null);
      await store.refresh(graph.id);
    })));
  }

  create(type) {
    return this.actions.guard(() => {
      const note = type === 'note';
      this.newType = type;
      this.creatingIn = this.store.graph.id;
      byId('item-dialog-title').textContent = note ? '新建笔记' : '新建工单';
      byId('new-note-fields').hidden = !note;
      this.itemControls.namedItem('title').value = '';
      this.itemControls.namedItem('id').value = '';
      this.itemControls.namedItem('kind').value = 'note';
      this.itemControls.namedItem('in_graph').checked = true;
      showDialog(byId('item-dialog'));
    });
  }

  renderGraphs() {
    const {graph, graphs} = this.store;
    const choices = graphs.map(row => [row.id, row.id === graph?.id ? graph.name : row.name]);
    if (graph && !graphs.some(row => row.id === graph.id)) choices.push([graph.id, graph.name]);
    options(this.graphSelect, choices, graph?.id);
    this.graphSelect.disabled = !choices.length;
    const names = [...new Set(graph?.tickets.map(row => row.group_name) || [])].sort((a, b) => a.localeCompare(b));
    const groups = [['', '全部分组'], ...names.map(name => [groupValue(name), name || '未分组'])];
    if (!groups.some(([value]) => value === this.store.view.group)) this.store.view.group = '';
    options(this.group, groups, this.store.view.group);
    byId('rename-graph').disabled = !graph;
    byId('delete-graph').disabled = !graph || graph.id === 'default';
  }

  itemButton(type, row, detail = '') {
    const {selection} = this.store;
    const key = targetName(type, row.id);
    const active = selection?.id === row.id && selection.type === type;
    const node = button(row.title, () => this.actions.pickNode(type, row.id), {
      className: 'item-button', 'aria-current': active ? 'true' : null, title: row.title,
      'data-item': `${type}:${row.id}`,
    });
    node.prepend(element('span', {className: 'item-id', text: key}));
    if (detail) node.append(element('span', {className: 'item-detail', text: detail}));
    return node;
  }

  renderItems() {
    const {graph, view, collapsed, proposals, selection} = this.store;
    if (!graph) return;
    const focus = document.activeElement;
    const focusRegion = focus?.closest('#navigator, #list-view');
    const focusKey = focus?.dataset.item;
    if (this.search.value !== view.search) this.search.value = view.search;
    this.status.value = view.status;
    this.group.value = view.group;
    byId('graph-mode').setAttribute('aria-pressed', view.mode === 'graph');
    byId('list-mode').setAttribute('aria-pressed', view.mode === 'list');

    const {matching, included: shown, notes} = filterItems(graph, view);
    const groups = new Map();
    const children = new Map();
    for (const row of graph.tickets) {
      if (!shown.has(row.id)) continue;
      const index = shown.has(row.parent_id) ? children : groups;
      const key = index === children ? row.parent_id : row.group_name;
      if (!index.has(key)) index.set(key, []);
      index.get(key).push(row);
    }
    const toggle = (key, label) => button(collapsed.has(key) ? '+' : '−', () => this.actions.collapse(key), {
      className: 'tree-toggle', 'aria-expanded': !collapsed.has(key), 'aria-label': label,
      'data-item': `toggle:${key}`,
    });
    const tree = rows => element('ul', {className: 'ticket-branches'}, ...rows.map(row => {
      const key = `t:${row.id}`;
      const branch = children.get(row.id);
      const item = this.itemButton('ticket', row);
      return element('li', {},
        element('div', {className: 'tree-row'},
          ...(branch ? [toggle(key, row.title)] : []), item),
        ...(branch && !collapsed.has(key) ? [tree(branch)] : []));
    }));
    byId('ticket-tree').replaceChildren(...(groups.size ?
      [...groups].sort(([a], [b]) => a.localeCompare(b)).map(([name, rows]) => {
        const key = groupKey(name);
        const title = name || '未分组';
        return element('div', {className: 'tree-group'},
          element('div', {className: 'tree-group-name'}, toggle(key, title), element('span', {text: title})),
          ...(!collapsed.has(key) ? [tree(rows)] : []));
      }) : [empty('没有符合条件的工单')]));
    for (const [id, active] of [['note-list', true], ['reference-list', false]]) {
      const rows = notes.filter(row => row.in_graph === active);
      byId(id).replaceChildren(...(rows.length ? rows.map(row => this.itemButton('note', row, row.kind)) :
        [empty('没有符合条件的笔记')]));
    }
    byId('proposal-list').replaceChildren(...(proposals.length ? proposals.map(proposal =>
      button(proposal.summary, () => this.actions.run(() => this.actions.select({type: 'proposal', id: proposal.id})), {
        className: 'item-button proposal-item', 'aria-current': selection?.id === proposal.id ? 'true' : null,
        title: proposal.applicable ? '待审核' : '待审核 · 当前不可应用',
        'data-item': `proposal:${proposal.id}`,
      })) : [empty('没有待审提案')]));
    if (view.mode === 'list') this.renderTable(matching, notes);
    if (focusKey && focusRegion && !focus.isConnected) {
      [...focusRegion.querySelectorAll('[data-item]')].find(node => node.dataset.item === focusKey)
        ?.focus({preventScroll: true});
    }
  }

  renderTable(tickets, notes) {
    const list = byId('list-view');
    const rows = [
      ...tickets.map(row => ['ticket', row, statusText.get(row.status), row.priority, row.group_name]),
      ...notes.map(row => ['note', row, row.state, row.kind, row.in_graph ? '图谱笔记' : '概览 / 参考']),
    ];
    if (!rows.length) {
      list.replaceChildren(empty('没有符合条件的项目'));
      return;
    }
    list.replaceChildren(element('table', {className: 'item-table'},
      element('thead', {}, element('tr', {}, ...['项目', '状态', '优先级 / 类型', '分组 / 位置']
        .map(text => element('th', {scope: 'col', text})))),
      element('tbody', {}, ...rows.map(([type, row, ...columns]) => element('tr', {},
        element('td', {}, this.itemButton(type, row)), ...columns.map(text => element('td', {text})))))));
  }
}
