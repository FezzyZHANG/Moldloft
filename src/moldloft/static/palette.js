import {matchesItem} from './layout.js';
import {readTarget, targetName} from './store.js';
import {byId, element} from './ui.js';

export class Palette {
  constructor(store, actions) {
    this.store = store;
    this.actions = actions;
    this.dialog = byId('command-palette');
    this.query = byId('palette-query');
    this.list = byId('palette-results');
    this.status = byId('palette-status');
    this.request = 0;
    byId('open-palette').addEventListener('click', () => this.open());
    byId('close-palette').addEventListener('click', () => this.dialog.close());
    this.dialog.addEventListener('close', () => {
      clearTimeout(this.timer);
      this.request++;
      this.query.setAttribute('aria-expanded', 'false');
    });
    this.query.addEventListener('input', () => this.search());
    this.dialog.addEventListener('keydown', event => this.keyboard(event));
    document.addEventListener('keydown', event => {
      if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 'k' || event.isComposing) return;
      if (document.querySelector('dialog[open]') && !this.dialog.open) return;
      event.preventDefault();
      if (this.dialog.open) this.dialog.close();
      else this.open();
    });
  }

  open() {
    if (this.dialog.open) return;
    this.query.value = '';
    this.dialog.showModal();
    this.query.focus();
    this.search();
  }

  commands() {
    const {store, actions} = this;
    const rows = store.graph ? [
      ['ticket', '新建工单', 'New Ticket', () => actions.create('ticket')],
      ['note', '新建笔记', 'New Note', () => actions.create('note')],
      ['verify', '校验 Ground Truth', 'Verify Ground Truth', actions.verify],
      ['fit', '适应画布', 'Fit Canvas', actions.fit],
      ['list', '切换图谱 / 表格', 'Toggle List View', () => store.updateView({mode: store.view.mode === 'list' ? 'graph' : 'list'})],
      ['local', '切换局部图谱', 'Toggle Local Graph', () => store.updateView({mode: 'graph', local: !store.view.local})],
    ] : [];
    for (const graph of store.graphs) {
      if (graph.id !== store.graph?.id) rows.push([
        `graph:${graph.id}`, `切换到 ${graph.name}`, 'Switch Graph', () => actions.openGraph(graph.id),
      ]);
    }
    return rows.map(([key, title, detail, action]) => ({key, title, detail, action}));
  }

  item(type, id, title, summary = '') {
    const target = {type, id};
    return {key: `${type}:${id}`, title, detail: targetName(type, id),
      summary, action: () => this.actions.select(target, {reveal: true})};
  }

  search() {
    clearTimeout(this.timer);
    const request = ++this.request;
    const query = this.query.value.trim();
    const graph = this.store.graph;
    const commands = this.commands().filter(row => `${row.title} ${row.detail}`.toLowerCase().includes(query.toLowerCase()));
    const local = graph && query ? [
      ...graph.tickets.filter(row => matchesItem(row, query)).map(row => this.item('ticket', row.id, row.title)),
      ...graph.notes.filter(row => matchesItem(row, query)).map(row => this.item('note', row.id, row.title)),
    ].slice(0, 30) : [];
    this.render([...commands, ...local], null);
    this.status.textContent = '';
    if (!query || !graph) return;
    this.timer = setTimeout(async () => {
      try {
        const {results} = await this.store.search(query.replace(/^#(?=\d+$)/, ''), {graph: graph.id});
        if (request !== this.request || graph.id !== this.store.graph?.id) return;
        const combined = new Map([...commands, ...local].map(row => [row.key, row]));
        for (const hit of results) {
          const target = readTarget(String(hit.item_id));
          if (!target || target.type !== hit.item_type) continue;
          const row = this.item(target.type, target.id, hit.title, hit.summary);
          combined.set(row.key, row);
        }
        this.render([...combined.values()], this.rows[this.index]?.key);
      } catch (error) {
        if (request === this.request) this.status.textContent = error.message;
      }
    }, 150);
  }

  render(rows, active) {
    this.rows = rows;
    this.index = Math.max(0, rows.findIndex(row => row.key === active));
    this.list.replaceChildren(...rows.map((row, index) => {
      const option = element('div', {id: `palette-option-${index}`, className: 'palette-option', role: 'option'},
        element('span', {text: row.title}), element('small', {text: row.detail}),
        ...(row.summary ? [element('small', {className: 'palette-summary', text: row.summary})] : []));
      option.addEventListener('pointerdown', event => event.preventDefault());
      option.addEventListener('click', () => this.choose(index));
      return option;
    }));
    if (!rows.length) this.list.append(element('p', {className: 'empty', text: '没有匹配项'}));
    this.highlight();
  }

  highlight(scroll = false) {
    const active = this.rows.length ? `palette-option-${this.index}` : '';
    for (const option of this.list.querySelectorAll('[role=option]')) {
      option.setAttribute('aria-selected', option.id === active);
    }
    this.query.setAttribute('aria-expanded', !!active);
    if (active) {
      this.query.setAttribute('aria-activedescendant', active);
      if (scroll) byId(active).scrollIntoView({block: 'nearest'});
    } else this.query.removeAttribute('aria-activedescendant');
  }

  choose(index) {
    const row = this.rows[index];
    if (!row) return;
    this.dialog.close();
    this.actions.run(row.action);
  }

  keyboard(event) {
    if (!this.rows.length || event.isComposing || event.target !== this.query) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const step = event.key === 'ArrowDown' ? 1 : -1;
      this.index = (this.index + this.rows.length + step) % this.rows.length;
      this.highlight(true);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      this.choose(this.index);
    }
  }
}
