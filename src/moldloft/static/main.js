import {Canvas} from './canvas.js';
import {Inspector} from './inspector.js';
import {filterItems, groupKey, groupValue, matchesItem, matchesStatus, previewOverlay, project, ticketKey} from './layout.js';
import {Palette} from './palette.js';
import {Review} from './review.js';
import {Sidebar} from './sidebar.js';
import {findItem, readTarget, targetParam, WorkbenchStore} from './store.js';
import {button, byId} from './ui.js';

function report(error) {
  byId('notice-text').textContent = error.message || String(error);
  byId('notice').hidden = false;
}

const run = async action => {
  try { await action(); }
  catch (error) { report(error); }
};

const store = new WorkbenchStore(report);
const actions = {
  report, run,
  guard: action => inspector.beforeAction(action),
  create: type => sidebar.create(type),
  openGraph, select, pickNode,
  reference: value => run(() => select(readTarget(value), {reveal: true})),
  collapse: key => {
    if (store.collapsed.has(key)) store.collapsed.delete(key);
    else store.collapsed.add(key);
    store.emit('collapse');
  },
  connect: id => store.updateView({mode: 'graph', connect: {id, key: ticketKey(id)}}),
  verify: () => store.verifyGraph(),
  fit: () => { store.updateView({mode: 'graph'}); canvas.fit(); },
};
const sidebar = new Sidebar(store, actions);
const inspector = new Inspector(store, actions);
const review = new Review(store, actions);
const canvas = new Canvas(actions);
new Palette(store, actions);
let navigation = 0;

function readLocation() {
  const params = new URLSearchParams(location.search);
  return {graph: params.get('graph'), item: readTarget(params.get('item'))};
}

function writeLocation(replace = false) {
  if (!store.graph) return;
  const url = new URL(location.href);
  url.searchParams.set('graph', store.graph.id);
  const item = targetParam(store.selection);
  if (item) url.searchParams.set('item', item);
  else url.searchParams.delete('item');
  if (url.href !== location.href) history[replace ? 'replaceState' : 'pushState'](null, '', url);
}

async function openGraph(id, target, {replace = false} = {}) {
  if (id === store.graph?.id) return select(target || null, {replace});
  const request = ++navigation;
  await actions.guard(async () => {
    if (request !== navigation) return;
    if (!await store.open(id, target, () => actions.guard(() => request === navigation))) return;
    if (store.selectedKey) canvas.reveal(store.selectedKey);
    writeLocation(replace);
  });
}

function reveal(target) {
  const row = findItem(store.graph, target);
  if (!row) return;
  const view = store.view;
  if (!matchesItem(row, view.search)) view.search = '';
  if (target.type === 'ticket') {
    if (!matchesStatus(row, view.status)) view.status = '';
    if (view.group && groupValue(row.group_name) !== view.group) view.group = '';
    store.collapsed.delete(groupKey(row.group_name));
    const byId = new Map(store.graph.tickets.map(ticket => [ticket.id, ticket]));
    let item = row;
    while (item) {
      store.collapsed.delete(ticketKey(item.id));
      item = byId.get(item.parent_id);
    }
  } else if (row.in_graph) {
    view.showNotes = true;
    if ((view.status || view.group) && !filterItems(store.graph, view).notes.includes(row)) {
      view.status = view.group = '';
    }
  } else view.local = false;
}

async function select(target, {reveal: show = false, replace = false} = {}) {
  const request = ++navigation;
  await actions.guard(() => {
    if (request !== navigation) return;
    if (target && target.type !== 'proposal' && !findItem(store.graph, target)) {
      throw new Error('当前图谱没有此项目');
    }
    if (show) reveal(target);
    if (target?.type !== store.selection?.type || target?.id !== store.selection?.id) {
      if (target?.type === 'proposal') {
        store.view.mode = 'graph';
        store.view.connect = null;
      }
      store.select(target);
    } else if (show) store.emit('view');
    if (show) canvas.reveal(store.selectedKey);
    writeLocation(replace);
  });
}

function pickNode(type, id) {
  return run(async () => {
    const {connect} = store.view;
    if (type === 'ticket' && typeof id !== 'number') return;
    if (type === 'note' && !findItem(store.graph, {type, id}) && store.inspection) return;
    if (connect && type === 'ticket') {
      if (connect.id === id) return;
      await actions.guard(async () => {
        await store.mutate([{op: 'edge_add', blocker_id: connect.id, blocked_id: id}]);
        store.updateView({connect: null});
        await store.refresh();
      });
    }
    await select({type, id}, {reveal: true});
  });
}

function renderHeader() {
  const {graph, connection, drift} = store;
  const indicator = byId('connection');
  indicator.dataset.state = connection;
  indicator.textContent = {online: '实时连接', connecting: '正在连接', offline: '离线轮询'}[connection];
  const count = [...drift.values()].filter(result => ['drifted', 'missing'].includes(result.status)).length;
  const badge = byId('drift-count');
  badge.hidden = !count;
  badge.textContent = count;
  byId('pending-count').textContent = graph?.pending_proposals ?? 0;
  for (const id of ['verify-graph', 'pending-button', 'clear-proposals', 'new-ticket', 'new-note']) byId(id).disabled = !graph;
}

function renderCanvas() {
  const {graph, view, selectedKey} = store;
  if (!graph) return;
  const inspection = store.inspection?.status === 'pending' ? store.inspection : null;
  const show = view.mode === 'graph';
  byId('canvas-tools').hidden = !show;
  byId('canvas-frame').hidden = !show;
  byId('list-view').hidden = show;
  byId('local').checked = view.local;
  byId('direction').value = view.direction;
  byId('depth').value = view.depth;
  byId('direction').disabled = byId('depth').disabled = !view.local;
  byId('show-notes').checked = view.showNotes;
  const message = byId('canvas-message');
  message.hidden = !inspection && !view.connect && !(view.local && !selectedKey && show);
  if (view.connect) {
    message.replaceChildren(`#${view.connect.id} → 请选择后续工单`,
      button('取消连接', () => store.updateView({connect: null})));
  } else message.textContent = inspection ?
    (inspection.preview ? '提案预览 · 新增 / 修改 / 删除' : '此提案没有可显示的图谱预览') :
    '选择一个工单或图谱笔记以查看局部图谱';
  const current = previewOverlay(graph, inspection);
  const ready = graph.tickets.filter(row => row.ready).length;
  byId('graph-stats').textContent =
    `${graph.tickets.length} 工单 · ${ready} 可执行 · ${graph.notes.length} 笔记 · ${graph.edges.length} 依赖 · r${graph.revision}`;
  if (show) canvas.update(project(current, store.collapsed, {...view, drift: store.drift,
    focus: view.local ? selectedKey : null}), selectedKey, view.connect);
}

store.subscribe(reason => {
  if (reason === 'open') canvas.reset();
  if (['graphs', 'open', 'snapshot'].includes(reason)) sidebar.renderGraphs();
  if (['graphs', 'connection', 'open', 'snapshot', 'drift'].includes(reason)) renderHeader();
  if (reason === 'graphs' || reason === 'connection') return;
  if (reason !== 'drift' && reason !== 'inspection') sidebar.renderItems();
  inspector.render(reason);
  if (reason === 'open' || reason === 'selection') {
    review.select(store.selection?.type === 'proposal' ? store.selection : null);
  }
  if (reason === 'snapshot') void review.refresh();
  renderCanvas();
});

byId('new-ticket').addEventListener('click', () => run(() => actions.create('ticket')));
byId('new-note').addEventListener('click', () => run(() => actions.create('note')));
byId('verify-graph').addEventListener('click', () => run(actions.verify));
byId('fit').addEventListener('click', actions.fit);
byId('zoom-in').addEventListener('click', () => canvas.zoom(1.2));
byId('zoom-out').addEventListener('click', () => canvas.zoom(1 / 1.2));
for (const [id, key, read] of [
  ['local', 'local', node => node.checked], ['show-notes', 'showNotes', node => node.checked],
  ['direction', 'direction', node => node.value], ['depth', 'depth', node => Number(node.value)],
]) {
  byId(id).addEventListener('change', event => store.updateView({[key]: read(event.target)}));
}
const legend = document.querySelector('.legend');
legend.addEventListener('change', () => store.updateView({
  types: new Set([...legend.querySelectorAll(':checked')].map(input => input.value)),
}));
byId('dismiss-notice').addEventListener('click', () => { byId('notice').hidden = true; });

window.addEventListener('keydown', event => {
  if (event.key === 'Escape' && store.view.connect && !document.querySelector('dialog[open]')) {
    store.updateView({connect: null});
  }
});
window.addEventListener('beforeunload', event => {
  if (inspector.unsaved) {
    event.preventDefault();
    event.returnValue = true;
  }
});
window.addEventListener('popstate', () => run(async () => {
  const {graph, item} = readLocation();
  try { await openGraph(graph || 'default', item, {replace: true}); }
  catch (error) {
    writeLocation(true);
    throw error;
  }
}));

renderHeader();
void run(async () => {
  const {graph, item} = readLocation();
  const graphs = await store.listGraphs();
  const id = graphs.some(candidate => candidate.id === graph) ? graph : 'default';
  await openGraph(id, id === graph || !graph ? item : null, {replace: true});
});
