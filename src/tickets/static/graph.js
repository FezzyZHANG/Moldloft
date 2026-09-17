import {TicketDraft, AttentionSaver, fields} from './draft.js';
import {Diagram, statuses, priorities} from './canvas.js';
import {groupKey, previewOverlay, ticketKey, noteKey} from './layout.js';
import {NotesEditor} from './notes-editor.js';
import {matchesNote, noteKinds, notePreview, noteStates, relationNames} from './notes-data.js';
import {scopedUrl, setupDrydocks} from './drydocks.js';

const $ = id => document.getElementById(id);
const drydockId = document.body.dataset.drydockId;
const scope = path => scopedUrl(path, drydockId);
const translationEnabled = document.body.dataset.translationEnabled === 'true';
const labels = {title: '标题', description: '描述', tech_notes: '技术要点', status: '状态', priority: '优先级', group_name: '工单组', parent_id: '父工单'};
let graph = {revision: 0, tickets: [], edges: [], links: []}, selected = null, isNew = false;
let groupFilter, connecting = null, proposal = null, previewBase = null, proposalRequest = null;
let chatController = null, history = [], refreshing = null, refreshAgain = false, inAction = false;
let chatEpoch = 0, creating = false, createIntent = null, applying = false;
let notes, localGraph = false;
let review = null, reviewBase = null, proposalsQueue = {items: [], pending: 0}, reviewing = false;
const originLabels = {free: '一般变更', resolve: '前提整理'}, storeLabels = {tickets: '工单', notes: '笔记', mixed: '工单与笔记'};
const statusLabels = {pending: '待审', applied: '已应用', rejected: '已拒绝', expired: '已失效'};
const visibleRelations = new Set(Object.keys(relationNames));
const collapsed = new Set(), form = $('ticket-form'), region = $('editor-region'), dialog = $('link-dialog');
const newId = () => crypto.randomUUID();
function node(tag, text = '', cls = '') { const n = document.createElement(tag); n.textContent = text; if (cls) n.className = cls; return n; }
function button(text, action, cls = 'ghost') {
  const b = node('button', text, cls); b.type = 'button'; b.addEventListener('click', () => Promise.resolve().then(action).catch(report)); return b;
}
function report(error) { $('notice').textContent = error.message || String(error); $('notice').hidden = false; }
$('notice').onclick = () => { $('notice').hidden = true; };

async function api(path, options = {}) {
  const response = await fetch(scope(path), {cache: 'no-store', ...options,
    headers: {'Content-Type': 'application/json', ...options.headers}});
  let data;
  try { data = await response.json(); } catch { throw new Error('服务器响应不完整，请重试'); }
  if (!response.ok) {
    const error = new Error(data.message || (typeof data.detail === 'string' ? data.detail : '请求未通过验证'));
    error.status = response.status; error.data = data; throw error;
  }
  if (data.drydock_id && data.drydock_id !== drydockId) throw new Error('响应属于其他 Drydock，请重新载入当前图');
  return data;
}
const post = (path, data) => api(path, {method: 'POST', body: JSON.stringify(data)});

const draft = new TicketDraft(async (base, patch) => {
  if (isNew) throw new Error('请点击「创建工单」完成创建');
  const body = {fields: patch, version: base.version, request_id: newId()};
  if ('parent_id' in patch || 'group_name' in patch) body.revision = graph.revision;
  const encoded = JSON.stringify(body);
  const result = await api(`/api/tickets/${base.id}`, {method: 'PATCH', body: encoded,
    keepalive: new Blob([encoded]).size < 60000});
  const latest = await api('/api/graph');
  graph = latest;
  const ticket = graph.tickets.find(t => t.id === base.id);
  if (!ticket || ticket.version !== result.versions[String(base.id)]) {
    const error = new Error('保存后工单再次变化，请比较最新版本'); error.status = 409; error.data = {current: ticket}; throw error;
  }
  renderTree(); renderAux(); renderContext(); scheduleDraw();
  return ticket;
}, renderSaveState);

const owns = target => target instanceof Node && (region.contains(target) || dialog.contains(target));
const attention = new AttentionSaver(draft, () => owns(document.activeElement), report);
notes = new NotesEditor({graph: () => graph, api, owns, report, open: ref => runAction(() => openReference(ref)),
  saved: async () => { await refresh(); if (notes.active && !notes.isNew) historyReplace(null, notes.id); }});
const diagram = new Diagram($('dag'), id => runAction(() => typeof id === 'string' && id.startsWith('n-') ? selectNote(id) : selectTicket(id)), key => runAction(() => {
  if (collapsed.has(key)) collapsed.delete(key); else collapsed.add(key); draw();
}), edges => runAction(() => {
  if (!edges.length) return;
  if (edges[0].owner) { selectNote(edges[0].owner); $('note-relations').scrollIntoView({block: 'nearest'}); return; }
  selectTicket(edges[0].blocked_id);
  $('relationships').scrollIntoView({block: 'nearest'});
}));
let drawTimer;
function scheduleDraw() { clearTimeout(drawTimer); drawTimer = setTimeout(draw, 40); }
function draw() {
  const overlay = review?.preview ? previewOverlay(reviewBase, review.preview) : proposal && !proposalStale() ? previewOverlay(previewBase, proposal) : null;
  const displayed = overlay || graph;
  $('graph-count').textContent = `${graph.tickets.length} 工单 · ${(graph.notes || []).length} 笔记`;
  $('graph-hint').textContent = connecting ? `选择后续工单：#${connecting} → …（Esc 取消）` : overlay ? '正在预览提案；应用后才会保存' : '实线箭头指向后续工单 · 悬停查看邻居 · 点击连线查看关系';
  const focus = notes.id ? noteKey(notes.id) : selected ? ticketKey(selected) : null;
  $('local-graph').disabled = !focus;
  $('local-graph').setAttribute('aria-pressed', String(localGraph));
  $('scope-label').textContent = localGraph && focus ? `${notes.id ? '当前笔记' : '#' + selected} · ${$('local-depth').value} 层关系` : '全局';
  diagram.render(displayed, collapsed, {group: groupFilter, status: $('status-filter').value, search: $('search').value.trim(),
    focus: localGraph ? focus : null, depth: Number($('local-depth').value), types: visibleRelations, showNotes: $('show-notes').checked}).catch(report);
}
const proposalStale = () => proposal && (proposal.drydock_id !== drydockId || proposal.base_revision !== graph.revision || proposal.notes_revision !== graph.notes_revision);

async function runAction(action, propagate = false) {
  if (inAction) { if (propagate) throw new Error('正在保存，请等待确认'); return; }
  if (creating) {
    const error = new Error('正在创建工单，请等待服务器确认');
    if (propagate) throw error; report(error); return;
  }
  inAction = true;
  try {
    if (isNew && draft.dirty) throw new Error('新工单尚未创建，请先创建或取消');
    await notes.beforeAction();
    await attention.beforeAction(() => {});
    inAction = false;
    return await action();
  } catch (error) { if (propagate) throw error; report(error); }
  finally { inAction = false; }
}

function renderSaveState() {
  if (notes?.active) return;
  const state = draft?.state || 'clean';
  const text = {clean: '已保存', dirty: '编辑中 · 离开时保存', saving: '保存中…', conflict: '版本冲突', error: '保存未完成'};
  $('save-state').textContent = isNew ? '尚未创建' : selected ? text[state] : '';
  $('save-state').dataset.state = state;
  $('conflict').hidden = !draft?.conflict;
  if (draft?.conflict) {
    const current = draft.conflict;
    $('conflict-current').textContent = current.deleted ? '该工单已被删除。你的草稿仍在编辑区。' : current.unknown ? '请获取服务器版本，再比较你的修改。' : fields.map(f => `${labels[f]}：${displayValue(f, current[f])}`).join('\n');
    $('keep-draft').disabled = !!current.deleted;
  }
  if (state === 'clean' && draft.base && !isNew) {
    for (const field of fields) {
      const control = form.elements[field], value = String(draft.values[field] ?? '');
      if (control.value !== value) control.value = value;
    }
  }
}

function displayValue(field, value) {
  if (field === 'status') return statuses[value] || value;
  if (field === 'priority') return priorities[value] || value;
  if (field === 'parent_id') return value == null ? '无' : '#' + value;
  return value || '（空）';
}

function renderTree() {
  const root = $('tree'); root.replaceChildren();
  const groups = [...new Set(graph.tickets.map(t => t.group_name))].sort();
  for (const name of groups) {
    const section = node('div', '', 'tree-group'), heading = node('div', '', 'tree-heading');
    const list = graph.tickets.filter(t => t.group_name === name);
    heading.append(button(collapsed.has(groupKey(name)) ? '+' : '−', () => runAction(() => {
      const key = groupKey(name); if (collapsed.has(key)) collapsed.delete(key); else collapsed.add(key); renderTree(); draw();
    })), button(`${name || '未分组'} (${list.length})`, () => runAction(() => { groupFilter = name; draw(); })));
    section.append(heading);
    const append = (parent, depth) => {
      for (const ticket of list.filter(t => t.parent_id === parent)) {
        const b = button(`#${ticket.id} ${ticket.title}`, () => runAction(() => selectTicket(ticket.id)), `tree-ticket ${selected === ticket.id ? 'selected' : ''}`);
        b.style.paddingLeft = `${8 + Math.min(depth, 8) * 12}px`; b.title = ticket.title;
        section.append(b); append(ticket.id, depth + 1);
      }
    };
    append(null, 0); root.append(section);
  }
  $('group-list').replaceChildren(...groups.map(g => { const o = node('option'); o.value = g; return o; }));
  const list = $('notes-tree'); list.replaceChildren();
  for (const kind of Object.keys(noteKinds)) {
    const items = (graph.notes || []).filter(n => n.kind === kind && matchesNote(n, $('search').value));
    if (!items.length) continue;
    list.append(node('h3', `${noteKinds[kind]} · ${items.length}`));
    for (const n of items) {
      const entry = button('', () => runAction(() => selectNote(n.id)), `tree-note ${notes.id === n.id ? 'selected' : ''}`);
      entry.append(node('span', `◇ ${n.title}`, 'note-title'), node('small', n.abstract_zh || n.abstract_en || '摘要待补充', 'note-snippet'));
      entry.title = `${n.title}\n${noteStates[n.kind][n.state]}\n${notePreview(n)}`; list.append(entry);
    }
  }
  if (!list.childNodes.length) list.append(node('small', $('search').value.trim() ? '没有匹配的笔记。' : '把尚未成为任务的想法放在这里。'));
  $('notes-warning').hidden = !(graph.notes_errors || []).length;
  $('notes-warning').textContent = (graph.notes_errors || []).map(e => `${e.file}: ${e.message}`).join('\n');
}

function renderForm() {
  if (notes?.active) { notes.render(); return; }
  if (review) { form.hidden = true; $('select-hint').hidden = true; $('relationships').hidden = true; $('code-links').hidden = true; $('knowledge-links').hidden = true; renderReview(); return; }
  $('review-form').hidden = true;
  const active = !!draft.base;
  form.hidden = !active; $('select-hint').hidden = active;
  $('relationships').hidden = !active || isNew; $('code-links').hidden = !active || isNew;
  if (!active) { $('editor-heading').textContent = '工单与笔记'; $('save-state').textContent = ''; return; }
  $('editor-heading').textContent = isNew ? '新建工单' : `工单 #${selected}`;
  const parent = form.elements.parent_id;
  parent.replaceChildren(new Option('无父级', ''), ...graph.tickets.filter(t => t.id !== selected).map(t => new Option(`#${t.id} ${t.title.slice(0, 34)}`, t.id)));
  for (const field of fields) form.elements[field].value = draft.values[field] ?? '';
  form.elements.group_name.readOnly = draft.values.parent_id !== null;
  $('save-now').textContent = isNew ? '创建工单' : '保存'; $('delete-ticket').textContent = isNew ? '取消' : '删除';
  $('new-child').hidden = isNew; $('connect-node').hidden = isNew; $('translation').hidden = isNew;
  renderSaveState(); renderAux();
}

async function selectTicket(id) {
  if (typeof id !== 'number') { report(new Error('这是尚未应用的预览节点')); return; }
  if (connecting !== null) {
    if (connecting === id) throw new Error('请选择另一张工单');
    await mutate([{op: 'edge_add', blocker_id: connecting, blocked_id: id}]); connecting = null; draw(); return;
  }
  const ticket = graph.tickets.find(t => t.id === id);
  if (!ticket) throw new Error('工单已不存在');
  collapsed.delete(groupKey(ticket.group_name));
  let ancestor = ticket;
  while (ancestor?.parent_id != null) { collapsed.delete(ticketKey(ancestor.parent_id)); ancestor = graph.tickets.find(t => t.id === ancestor.parent_id); }
  notes.hide(); leaveReview();
  selected = id; isNew = false; draft.load(ticket); renderForm(); renderTree(); renderContext(); diagram.select(id);
  historyReplace(id); draw();
}
function leaveReview() { review = null; reviewBase = null; $('review-form').hidden = true; renderProposals(); }
function historyReplace(id, note = null, proposalId = null) {
  const url = new URL(location.href);
  if (id) url.searchParams.set('ticket', id); else url.searchParams.delete('ticket');
  if (note) url.searchParams.set('note', note); else url.searchParams.delete('note');
  if (proposalId) url.searchParams.set('proposal', proposalId); else url.searchParams.delete('proposal');
  window.history.replaceState(null, '', url);
}
function selectNote(id) {
  if (connecting !== null) throw new Error('执行前置只连接工单；请先按 Esc 取消');
  const note = graph.notes.find(n => n.id === id);
  if (!note) throw new Error('笔记不存在或暂时无法读取');
  leaveReview(); selected = null; isNew = false; draft.load(null); notes.show(note);
  renderTree(); renderKnowledge(); renderContext(); diagram.select(id); historyReplace(null, id); draw();
}
function openReference(ref) {
  if (!ref) { $('editor-heading').textContent = '工单与笔记'; $('save-state').textContent = ''; renderForm(); renderKnowledge(); renderTree(); renderContext(); historyReplace(null); return; }
  return ref.startsWith('ticket:') ? selectTicket(Number(ref.slice(7))) : selectNote(ref.slice(5));
}
function startNote(target = null) {
  connecting = null; leaveReview(); selected = null; isNew = false; draft.load(null); notes.start(target);
  renderKnowledge(); renderTree(); renderContext(); diagram.select(null); historyReplace(null);
}
function startNew(parent = null) {
  notes.hide(); leaveReview();
  selected = null; isNew = true; createIntent = null;
  const group = graph.tickets.find(t => t.id === parent)?.group_name || groupFilter || '';
  draft.load({id: 'new:ticket', title: '', description: '', tech_notes: '', status: 'open', priority: 'medium', group_name: group, parent_id: parent, version: 0});
  renderForm(); renderContext(); diagram.select(null); form.elements.title.focus();
}

async function refresh() {
  if (refreshing) { refreshAgain = true; return refreshing; }
  refreshing = (async () => {
    const next = await api('/api/graph'), oldRevision = graph.revision, oldNotesRevision = graph.notes_revision;
    graph = next;
    if (selected && !isNew && !draft.pending) {
      const ticket = graph.tickets.find(t => t.id === selected);
      const wasDirty = draft.dirty || draft.conflict;
      draft.observe(ticket || null);
      if (!wasDirty) { if (!ticket) selected = null; renderForm(); }
    }
    const wasNoteActive = notes.active;
    notes.observe();
    if (wasNoteActive && !notes.active) renderForm();
    if (proposal) renderProposal();
    await refreshProposals();
    renderTree(); renderAux(); renderContext();
    if (next.revision !== oldRevision || next.notes_revision !== oldNotesRevision || !diagram.layout) scheduleDraw();
  })().finally(() => { refreshing = null; if (refreshAgain) { refreshAgain = false; refresh().catch(report); } });
  return refreshing;
}

// Persistent proposals: review happens here, beside the graph, never on a separate page.
async function refreshProposals() {
  try {
    proposalsQueue = await api('/api/proposals');
    $('proposals-warning').hidden = !proposalsQueue.recovery_error;
    $('proposals-warning').textContent = proposalsQueue.recovery_error ? `写入暂停：${proposalsQueue.recovery_error}` : '';
  } catch (error) {
    proposalsQueue = {items: [], pending: 0};
    $('proposals-warning').hidden = false; $('proposals-warning').textContent = `提案列表暂不可用：${error.message}`;
  }
  if (review) {
    try { review = await api(`/api/proposals/${review.proposal.id}`); reviewBase = graph; }
    catch (error) { review = null; reviewBase = null; report(error); }
    renderForm(); scheduleDraw();
  }
  renderProposals();
}
function checkText(check) {
  if (!check) return '';
  return check.applicable ? `仍可应用 · ${check.mode === 'scoped' ? '按读依赖校验' : '按全局版本校验'}` : `已失效：${(check.reason || '').replace(/^Proposal expired: /, '')}`;
}
function renderProposals() {
  const pending = proposalsQueue.pending || 0;
  $('proposal-count').hidden = !pending; $('proposal-count').textContent = `待审提案 ${pending}`;
  $('proposals-count').textContent = pending ? `${pending} 项待审` : '暂无待审';
  const list = $('proposals-tree'); list.replaceChildren();
  const groups = new Map();
  for (const item of proposalsQueue.items || []) {
    const key = item.groups?.length ? item.groups.join(' · ') : '未分组';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  for (const [name, items] of [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0], 'zh'))) {
    list.append(node('h3', `${name} · ${items.length}`));
    for (const item of items) {
      const stale = item.check && !item.check.applicable;
      const entry = button('', () => runAction(() => selectProposal(item.id)), `tree-proposal ${review?.proposal.id === item.id ? 'selected' : ''} ${stale ? 'stale' : ''}`);
      entry.append(node('span', item.summary, 'proposal-summary'),
        node('small', `${originLabels[item.origin] || item.origin} · ${storeLabels[item.store] || ''} ${item.operation_count} 项 · ${item.created_at.slice(5, 16).replace('T', ' ')}${item.check ? ' · ' + checkText(item.check) : ''}`, 'proposal-meta'));
      entry.title = item.summary; list.append(entry);
    }
  }
  if (!list.childNodes.length) list.append(node('small', '没有待审提案。agent 通过 MCP 提交后会出现在这里。'));
}
async function selectProposal(id) {
  if (connecting !== null) throw new Error('请先按 Esc 取消连接');
  notes.hide(); selected = null; isNew = false; draft.load(null);
  review = await api(`/api/proposals/${id}`); reviewBase = graph;
  $('review-note').value = '';
  renderForm(); renderTree(); renderProposals(); renderContext(); diagram.select(null); historyReplace(null, null, id); draw();
}
function reviewEditable(op) {
  if (!['create', 'update', 'note_create', 'note_update'].includes(op.op)) return [];
  return Object.entries(op.fields || {}).filter(([field, value]) => typeof value === 'string' && !['relations', 'parent_id'].includes(field));
}
function renderReview() {
  const p = review.proposal, check = review.check, result = p.result || {};
  $('editor-heading').textContent = '提案审阅'; $('save-state').textContent = '';
  $('review-form').hidden = false;
  $('review-summary').textContent = p.summary || '（已清理的提案）';
  $('review-meta').textContent = p.cleared ? `${statusLabels[p.status] || p.status} · 记录已清理，仅保留回执` :
    `${originLabels[p.origin] || p.origin} · 来源 ${p.source} · ${p.created_at.slice(0, 16).replace('T', ' ')} · 存储状态 ${statusLabels[p.status] || p.status} · 读依据 ${Object.keys(p.basis?.tickets || {}).length} 工单 / ${Object.keys(p.basis?.notes || {}).length} 笔记`;
  const checkEl = $('review-check'); checkEl.className = '';
  if (p.status === 'pending' && check) {
    checkEl.textContent = check.applicable ? checkText(check) : `${checkText(check)} · 存储状态仍为待审；应用或拒绝时才会写入失效`;
    checkEl.classList.add(check.applicable ? 'ok' : 'stale');
  } else if (p.status === 'applied') {
    checkEl.textContent = `已应用 · revision ${p.applied_revision}${result.created && Object.keys(result.created).length ? ' · 新建 ' + Object.values(result.created).map(i => '#' + i).join('、') : ''}${result.notes?.length ? ' · 笔记 ' + result.notes.join('、') : ''}${result.note ? '\n审阅说明：' + result.note : ''}${result.amendments?.length ? '\n应用前修改了 ' + result.amendments.map(a => a.target).join('、') : ''}`;
    checkEl.classList.add('ok');
  } else if (p.status === 'rejected') {
    checkEl.textContent = `已拒绝${result.reason ? '：' + result.reason : ''}`; checkEl.classList.add('stale');
  } else {
    checkEl.textContent = `已失效：${p.stale_reason || ''}`; checkEl.classList.add('stale');
  }
  $('review-rationale').textContent = p.rationale || '';
  const sections = $('review-sections'); sections.replaceChildren();
  for (const section of review.sections || []) {
    const item = node('div', '', 'change');
    item.append(node('strong', `${section.target} · ${{created: '新增', updated: '修改', deleted: '删除', changed: '变更'}[section.action] || section.action}`));
    for (const line of section.lines) item.append(node('div', line.text, `line ${{removed: 'before', added: 'after'}[line.kind] || line.kind}`));
    sections.append(item);
  }
  if (!sections.childNodes.length) sections.append(node('small', p.status === 'pending' ? '差异暂时无法针对当前图渲染。' : '结果已记录；差异见 tracked 文件。'));
  const editable = p.status === 'pending' && check?.applicable;
  const edits = $('review-edits');
  const typed = new Map([...edits.querySelectorAll('textarea')].filter(t => t.value !== t.dataset.proposed).map(t => [`${t.dataset.index}:${t.dataset.field}`, t.value]));
  edits.replaceChildren();
  $('review-edit').hidden = !editable;
  if (editable) {
    (p.operations || []).forEach((op, index) => {
      const fields = reviewEditable(op); if (!fields.length) return;
      edits.append(node('strong', `${index + 1}. ${op.op} ${op.ticket_id !== undefined ? '#' + op.ticket_id : op.note_id || op.temp_id || ''}`));
      for (const [field, value] of fields) {
        const label = node('label', field), area = node('textarea');
        area.rows = ['description', 'tech_notes', 'body'].includes(field) ? 6 : 2;
        area.dataset.index = index; area.dataset.field = field; area.dataset.proposed = value;
        area.value = typed.has(`${index}:${field}`) ? typed.get(`${index}:${field}`) : value;
        label.append(area); edits.append(label);
      }
    });
  }
  $('review-apply').disabled = !editable || reviewing; $('review-reject').disabled = p.status !== 'pending' || reviewing;
  $('review-file').textContent = p.cleared ? '' : `tracked 文件：review/${p.created_at.slice(0, 10)}-${p.id}.md`;
}
function collectEdits() {
  const edits = new Map();
  for (const area of $('review-edits').querySelectorAll('textarea')) {
    if (area.value === area.dataset.proposed) continue;
    const index = Number(area.dataset.index);
    if (!edits.has(index)) edits.set(index, {index, fields: {}});
    edits.get(index).fields[area.dataset.field] = area.value;
  }
  return [...edits.values()];
}
$('review-form').addEventListener('submit', event => event.preventDefault());
function setReviewBusy(busy) { reviewing = busy; $('review-apply').disabled = busy; $('review-reject').disabled = busy; }
$('review-apply').onclick = () => runAction(async () => {
  if (!review || reviewing) return;
  // Read the reviewer's input before any re-render can rebuild the edit fields.
  const edits = collectEdits(), note = $('review-note').value.trim();
  setReviewBusy(true);
  try {
    const outcome = await post(`/api/proposals/${review.proposal.id}/apply`, {edits, note});
    $('review-note').value = '';
    review = {proposal: outcome, check: null, preview: null, sections: null}; reviewBase = null;
    setReviewBusy(false); await refresh();
  } catch (error) {
    setReviewBusy(false);
    if (error.status === 409 && error.data?.proposal) {
      review = {proposal: error.data.proposal, check: null, preview: null, sections: null}; reviewBase = null; await refresh();
    }
    throw error;
  } finally { if (review) renderReview(); draw(); }
});
$('review-reject').onclick = () => runAction(async () => {
  if (!review || reviewing) return;
  const reason = $('review-note').value.trim();
  if (!reason) throw new Error('拒绝需要填写理由；agent 会通过 get_proposal 读到它');
  setReviewBusy(true);
  try {
    const outcome = await post(`/api/proposals/${review.proposal.id}/reject`, {reason});
    $('review-note').value = '';
    review = {proposal: outcome, check: null, preview: null, sections: null}; reviewBase = null;
    setReviewBusy(false); await refresh();
  } catch (error) { setReviewBusy(false); throw error; }
  finally { if (review) renderReview(); draw(); }
});
$('review-close').onclick = () => runAction(() => { leaveReview(); renderForm(); renderProposals(); historyReplace(null); draw(); });
$('proposal-count').onclick = () => { $('proposals-tree').scrollIntoView({block: 'start', behavior: 'smooth'}); };
async function mutate(operations) {
  const result = await post('/api/mutations', {operations, revision: graph.revision, request_id: newId()});
  await refresh(); return result;
}

function renderAux() {
  renderKnowledge();
  if (selected === null || isNew) return;
  const ticket = graph.tickets.find(t => t.id === selected);
  if (!ticket) return;
  $('translation-title').textContent = ticket.title_zh || '尚无译文';
  $('translation-description').textContent = ticket.description_zh || '';
  $('translation-state').textContent = !translationEnabled ? '自动翻译已停用' : ticket.translation_due ? '等待处理' : ticket.translation_error ? '失败 · 译文可能过期' : ticket.translated_version === ticket.source_version ? '当前版本' : '版本未验证';
  $('translate-retry').disabled = !translationEnabled;
  const dependencies = $('dependency-list'); dependencies.replaceChildren();
  for (const edge of graph.edges.filter(e => e.blocker_id === selected || e.blocked_id === selected)) {
    const incoming = edge.blocked_id === selected, otherId = incoming ? edge.blocker_id : edge.blocked_id;
    const other = graph.tickets.find(t => t.id === otherId);
    const row = node('div', '', 'relation');
    row.append(node('span', `${incoming ? '前置' : '后续'} #${otherId} ${other?.title || ''} · ${statuses[other?.status] || ''}`, 'label'),
      button('打开', () => runAction(() => selectTicket(otherId))),
      button('移除', () => runAction(() => mutate([{op: 'edge_remove', ...edge}])))); dependencies.append(row);
  }
  if (!dependencies.childNodes.length) dependencies.append(node('small', '暂无依赖关系'));
  $('blocker-picker').replaceChildren(new Option('选择前置工单', ''), ...graph.tickets.filter(t => t.id !== selected).map(t => new Option(`#${t.id} ${t.title.slice(0, 30)}`, t.id)));
  const links = $('link-list'); links.replaceChildren();
  for (const link of graph.links.filter(l => l.ticket_id === selected)) {
    const row = node('div', '', 'link-row');
    row.append(node('span', `${link.repo} · ${link.path || link.ref.slice(0, 20)}${link.start_line ? ':' + link.start_line : ''}`, 'label'),
      button('查看', async () => {
        const data = await api(`/api/git/links/${link.id}`);
        $('code-preview').hidden = false;
        $('code-preview').textContent = data.summary + '\n\n' + (data.text !== undefined ? data.text.split('\n').map((line, i) => `${data.start_line + i}  ${line}`).join('\n') : (data.files || []).join('\n')) + (data.working_changes ? '\n\n工作区有未提交修改' : '');
      }), button('移除', () => runAction(() => mutate([{op: 'link_remove', link_id: link.id}])))); links.append(row);
  }
}

function renderKnowledge() {
  const ref = notes.id ? `note:${notes.id}` : selected ? `ticket:${selected}` : null;
  $('knowledge-links').hidden = !ref || isNew || notes.isNew;
  const list = $('backlinks'); list.replaceChildren();
  const ticket = graph.tickets.find(t => t.id === selected);
  $('premise-warning').hidden = !ticket?.assumptions?.length;
  $('premise-warning').textContent = ticket?.review_notes?.length ? '有未验证、已否定或已撤回的前提；请复核当前方案。工单状态保持不变。' : '当前方案依赖已明确采纳的假设。';
  const references = (graph.relations || []).filter(r => r.source === ref || r.target === ref);
  for (const premise of ticket?.assumptions || []) {
    if (!references.some(r => r.source === `note:${premise}` && r.type === 'assumes')) references.push({source: `note:${premise}`, target: ref, type: 'assumes', inherited: true});
  }
  for (const relation of references) {
    const other = relation.source === ref ? relation.target : relation.source;
    const value = other.startsWith('ticket:') ? graph.tickets.find(t => t.id === Number(other.slice(7))) : (graph.notes || []).find(n => n.id === other.slice(5));
    const row = node('div', '', `relation backlink relation-${relation.type}`);
    row.append(node('span', '', 'line-swatch'), node('span', `${relationNames[relation.type]}${relation.inherited ? ' · 经由前置工单' : ''} · ${value?.title || other + '（目标缺失）'}`, 'label'));
    if (value) { const open = button('打开', () => runAction(() => openReference(other))); open.title = other.startsWith('note:') ? notePreview(value) : value.description?.slice(0, 320) || ''; row.append(open); }
    list.append(row);
  }
  if (!references.length) list.append(node('small', '暂无笔记引用。'));
}

form.addEventListener('input', event => {
  if (!fields.includes(event.target.name)) return;
  const value = event.target.name === 'parent_id' ? (event.target.value ? Number(event.target.value) : null) : event.target.value;
  draft.set(event.target.name, value);
  if (event.target.name === 'parent_id') {
    const parent = graph.tickets.find(t => t.id === value);
    form.elements.group_name.readOnly = value !== null;
    if (parent) { draft.set('group_name', parent.group_name); form.elements.group_name.value = parent.group_name; }
  }
});
form.addEventListener('submit', async event => {
  event.preventDefault(); if (creating) return;
  try {
    if (isNew) {
      creating = true; $('save-now').disabled = true;
      for (const control of form.elements) control.disabled = true;
      const signature = JSON.stringify(draft.values);
      if (createIntent?.signature !== signature) createIntent = {signature, id: newId()};
      const result = await post('/api/tickets', {fields: draft.values, revision: graph.revision, request_id: createIntent.id});
      isNew = false; draft.load(null); await refresh(); await selectTicket(result.created['new:ticket']);
    } else await draft.flush();
  } catch (error) { report(error); }
  finally {
    if (creating) for (const control of form.elements) control.disabled = false;
    creating = false; $('save-now').disabled = false;
  }
});
region.addEventListener('compositionstart', () => { attention.composing = true; notes.attention.composing = true; });
region.addEventListener('compositionend', () => { attention.compositionEnd().catch(() => {}); notes.attention.compositionEnd().catch(() => {}); });
document.addEventListener('pointerdown', event => {
  if (!isNew && !owns(event.target) && draft.dirty) attention.leave().catch(() => {});
  if (!notes.isNew && !owns(event.target) && notes.draft.dirty) notes.attention.leave().catch(() => {});
}, true);
document.addEventListener('focusin', event => {
  if (!isNew && !owns(event.target) && draft.dirty) attention.leave().catch(() => {});
  if (!notes.isNew && !owns(event.target) && notes.draft.dirty) notes.attention.leave().catch(() => {});
});
const leaveWindow = () => {
  if (!isNew && draft.dirty) attention.leave().catch(() => {});
  if (!notes.isNew && notes.draft.dirty) notes.attention.leave().catch(() => {});
};
window.addEventListener('blur', leaveWindow);
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') leaveWindow(); else refresh().catch(report); });
window.addEventListener('pagehide', leaveWindow);
window.addEventListener('beforeunload', event => {
  if (draft.dirty || draft.pending || notes.draft.dirty || notes.draft.pending || notes.creating) { leaveWindow(); event.preventDefault(); event.returnValue = ''; }
});
window.addEventListener('keydown', event => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); if (!attention.composing) (notes.active ? notes.form : form).requestSubmit(); }
  if (event.key === 'Escape' && connecting !== null) { connecting = null; draw(); }
});

$('new-ticket').onclick = () => runAction(() => startNew());
$('new-note').onclick = () => runAction(() => startNote());
$('capture-note').onclick = () => runAction(() => startNote(notes.id ? `note:${notes.id}` : `ticket:${selected}`));
$('new-child').onclick = () => runAction(() => startNew(selected));
$('connect-node').onclick = () => runAction(() => { connecting = selected; draw(); });
$('delete-ticket').onclick = async () => {
  if (isNew) { isNew = false; draft.load(null); renderForm(); return; }
  await runAction(async () => {
    if (!confirm(`删除工单 #${selected}？子工单会保留，相关依赖会移除。`)) return;
    await mutate([{op: 'delete', ticket_id: selected}]); selected = null; draft.load(null); renderForm(); historyReplace(null);
  });
};
$('add-blocker').onclick = () => runAction(() => {
  const blocker = Number($('blocker-picker').value);
  if (!blocker) throw new Error('请选择前置工单');
  return mutate([{op: 'edge_add', blocker_id: blocker, blocked_id: selected}]);
});
$('translate-retry').onclick = () => runAction(async () => { await post(`/api/tickets/${selected}/translation`, {}); await refresh(); });
$('use-server').onclick = async () => {
  try { const latest = await api('/api/graph'); graph = latest; draft.load(latest.tickets.find(t => t.id === selected) || null); if (!draft.base) selected = null; renderForm(); draw(); }
  catch (error) { report(error); }
};
$('keep-draft').onclick = async () => {
  try {
    graph = await api('/api/graph'); const current = graph.tickets.find(t => t.id === selected);
    if (!current) throw new Error('工单已被删除，请复制草稿后新建');
    draft.rebase(current); renderForm(); await draft.flush();
  } catch (error) { report(error); }
};
$('search').oninput = () => { renderTree(); scheduleDraw(); };
$('status-filter').onchange = scheduleDraw;
$('clear-filter').onclick = () => runAction(() => { groupFilter = undefined; $('status-filter').value = ''; $('search').value = ''; renderTree(); draw(); });
$('fit').onclick = () => diagram.fit(); $('zoom-in').onclick = () => diagram.zoom(1 / 1.2); $('zoom-out').onclick = () => diagram.zoom(1.2);
$('collapse-groups').onclick = () => runAction(() => { graph.tickets.forEach(t => collapsed.add(groupKey(t.group_name))); renderTree(); draw(); });
$('expand-all').onclick = () => runAction(() => { collapsed.clear(); renderTree(); draw(); });
$('local-graph').onclick = () => runAction(() => { localGraph = !localGraph; collapsed.clear(); groupFilter = undefined; $('search').value = ''; $('status-filter').value = ''; diagram.frameLocal = localGraph; draw(); });
$('local-depth').onchange = () => { diagram.frameLocal = localGraph; draw(); };
$('show-notes').onchange = scheduleDraw;
for (const [type, title] of Object.entries(relationNames)) {
  const label = node('label', '', `relation-${type}`), input = node('input'); input.type = 'checkbox'; input.checked = true; input.value = type;
  input.onchange = () => { if (input.checked) visibleRelations.add(type); else visibleRelations.delete(type); draw(); };
  label.append(input, node('span', '', 'line-swatch'), document.createTextNode(title)); $('relation-legend').append(label);
}

$('add-link').onclick = () => { dialog.showModal(); loadRefs().catch(error => { $('link-error').textContent = error.message; }); };
$('close-link').onclick = () => dialog.close();
async function loadRefs() {
  $('link-error').textContent = ''; const refs = await api(`/api/git/${$('link-form').elements.repo.value}/refs`);
  $('ref-list').replaceChildren(...['HEAD', ...refs.branches, ...refs.commits.map(s => s.split('\t')[0])].map(ref => new Option(ref, ref)));
}
$('link-form').elements.repo.onchange = () => loadRefs().catch(error => { $('link-error').textContent = error.message; });
$('link-form').onsubmit = async event => {
  event.preventDefault();
  try {
    await attention.beforeAction(async () => {
      const values = Object.fromEntries(new FormData($('link-form')));
      values.start_line = values.start_line ? Number(values.start_line) : null;
      values.end_line = values.end_line ? Number(values.end_line) : null;
      await mutate([{op: 'link_add', ticket_id: selected, link: values}]); dialog.close();
    });
  } catch (error) { $('link-error').textContent = error.message; }
};

function references() { return [...new Set([...$('chat-input').value.matchAll(/#(\d+)/g)].map(m => Number(m[1])))]; }
function renderContext() {
  const seeds = new Set(references()); if (selected) seeds.add(selected);
  if (notes.id) {
    const active = (graph.notes || []).find(n => n.id === notes.id);
    for (const r of active?.relations || []) if (r.target.startsWith('ticket:') && graph.tickets.some(t => t.id === Number(r.target.slice(7)))) seeds.add(Number(r.target.slice(7)));
  }
  const ids = new Set(seeds);
  for (const t of graph.tickets) {
    if (seeds.has(t.id) && t.parent_id !== null) ids.add(t.parent_id);
    if (seeds.has(t.parent_id)) ids.add(t.id);
  }
  for (const e of graph.edges) if (seeds.has(e.blocker_id) || seeds.has(e.blocked_id)) { ids.add(e.blocker_id); ids.add(e.blocked_id); }
  $('chat-context').replaceChildren(...[...ids].sort((a, b) => a - b).map(id => node('span', `#${id}`)));
  const attached = new Set((graph.relations || []).filter(r => [...ids].some(id => r.target === `ticket:${id}`)).map(r => r.owner));
  for (const t of graph.tickets) if (ids.has(t.id)) for (const id of t.assumptions || []) attached.add(id);
  if (notes.id && !notes.isNew) attached.add(notes.id);
  for (const n of graph.notes || []) if (attached.has(n.id)) { const chip = node('span', `◇ ${n.title.slice(0, 24)}`); chip.title = n.title; $('chat-context').append(chip); }
}
$('chat-input').oninput = renderContext;
function message(role, content) {
  const row = node('div', '', `message ${role}`), label = node('span', role === 'user' ? '你' : role === 'error' ? '未完成' : 'Copilot', 'role');
  const body = node('span', content); row.append(label, body); $('messages').append(row);
  row.scrollIntoView({block: 'nearest'}); return body;
}
function renderProposal() {
  $('proposal').hidden = !proposal;
  if (!proposal) return;
  const stale = proposalStale();
  $('proposal-state').textContent = stale ? '数据已变化，请重新生成' : '尚未应用';
  $('apply-proposal').disabled = stale || !!chatController || applying;
  const container = $('proposal-changes'); container.replaceChildren();
  for (const change of proposal.changes) {
    const item = node('div', '', 'change');
    if (change.ticket_id !== undefined) {
      item.append(node('strong', `${{created: '新增', updated: '修改', deleted: '删除'}[change.kind]} #${change.ticket_id}`));
      for (const field of new Set([...Object.keys(change.before || {}), ...Object.keys(change.after || {})])) {
        if (change.before) item.append(node('div', `− ${labels[field]}：${displayValue(field, change.before[field])}`, 'before'));
        if (change.after) item.append(node('div', `＋ ${labels[field]}：${displayValue(field, change.after[field])}`, 'after'));
      }
    } else {
      item.append(node('strong', change.kind === 'edges' ? '依赖变更' : '代码关联变更'));
      const signature = value => change.kind === 'edges' ? `#${value.blocker_id} → #${value.blocked_id}` : `${value.repo} · ${value.path || value.ref}`;
      const a = new Set(change.before.map(signature)), b = new Set(change.after.map(signature));
      for (const value of a) if (!b.has(value)) item.append(node('div', '− ' + value, 'before'));
      for (const value of b) if (!a.has(value)) item.append(node('div', '＋ ' + value, 'after'));
    }
    container.append(item);
  }
}
async function consumeChat(response, onEvent) {
  if (!response.ok) { const body = await response.json(); throw new Error(body.message || '聊天请求失败'); }
  const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = '', completed = false;
  try {
    while (true) {
      const {value, done} = await reader.read(); if (done) break;
      buffer += decoder.decode(value, {stream: true}).replace(/\r\n/g, '\n');
      let index;
      while ((index = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, index); buffer = buffer.slice(index + 2);
        const data = frame.split('\n').filter(s => s.startsWith('data:')).map(s => s.slice(5).trimStart()).join('\n');
        if (!data) continue;
        const event = JSON.parse(data); if (event.type === 'done') completed = true; onEvent(event);
      }
    }
    if (!completed) throw new Error('响应未完整结束，可重新发送');
  } finally { reader.releaseLock(); }
}
$('chat-form').onsubmit = async event => {
  event.preventDefault(); if (chatController) return;
  await runAction(async () => {
    const input = $('chat-input').value.trim(); if (!input) return;
    const refs = references(), selection = selected, selectedNote = notes.id;
    const messages = [...history, {role: 'user', content: input}];
    message('user', input); const output = message('assistant', '');
    const controller = new AbortController(), epoch = chatEpoch; chatController = controller;
    $('chat-input').value = ''; $('stop-chat').hidden = false; $('send-chat').disabled = true; $('chat-state').textContent = '生成中…';
    let nextProposal = null, text = '';
    try {
      const response = await fetch(scope('/api/copilot/chat'), {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({messages, selected_id: selection, selected_note: selectedNote, references: refs}), signal: controller.signal});
      await consumeChat(response, event => {
        if (event.drydock_id && event.drydock_id !== drydockId) throw new Error('Copilot 响应属于其他 Drydock');
        if (event.type === 'text') { text += event.text; output.textContent = text; }
        if (event.type === 'context') $('chat-context').replaceChildren(...event.ticket_ids.map(id => node('span', '#' + id)),
          ...(event.note_ids || []).map(id => node('span', '◇ ' + (graph.notes.find(n => n.id === id)?.title.slice(0, 24) || id))));
        if (event.type === 'proposal') nextProposal = event;
        if (event.type === 'error') throw new Error(event.message);
      });
      if (controller.signal.aborted) throw new DOMException('Cancelled', 'AbortError');
      history = [...messages, {role: 'assistant', content: text + (nextProposal ? '\nPending preview (not applied): ' + JSON.stringify(nextProposal.operations) : '')}];
      if (nextProposal) {
        proposal = nextProposal; proposalRequest = newId(); previewBase = structuredClone(graph);
        renderProposal(); draw();
      }
      if (!text) output.textContent = nextProposal ? '修改已准备好，请查看预览。' : '没有返回内容。';
    } catch (error) {
      if (epoch === chatEpoch) {
        message('error', error.name === 'AbortError' ? '已停止，未应用任何修改。' : error.message);
        $('chat-input').value = input;
      }
    } finally {
      if (chatController === controller) {
        chatController = null; $('stop-chat').hidden = true; $('send-chat').disabled = false; $('chat-state').textContent = '';
        renderProposal(); renderContext();
      }
    }
  });
};
$('stop-chat').onclick = () => { chatController?.abort(); };
$('new-chat').onclick = () => {
  chatEpoch++; chatController?.abort(); chatController = null; history = []; proposal = null; previewBase = null;
  $('messages').replaceChildren(); $('chat-input').value = ''; $('stop-chat').hidden = true;
  $('send-chat').disabled = false; $('chat-state').textContent = '';
  renderProposal(); renderContext(); draw();
};
$('reject-proposal').onclick = () => {
  if (proposal) history.push({role: 'user', content: 'I rejected the pending preview. No changes were applied.'});
  proposal = null; previewBase = null; renderProposal(); draw();
};
$('apply-proposal').onclick = () => runAction(async () => {
  if (applying) return;
  if (!proposal || proposalStale()) throw new Error('提案已过期，请重新生成');
  applying = true; $('apply-proposal').disabled = true;
  try {
    await post('/api/copilot/apply', {operations: proposal.operations, revision: proposal.base_revision,
      drydock_id: proposal.drydock_id, context_ids: proposal.context_ids, request_id: proposalRequest, notes_revision: proposal.notes_revision});
    history.push({role: 'user', content: 'I applied the complete preview successfully.'});
    message('assistant', '整批修改已保存。'); proposal = null; previewBase = null;
    await refresh(); renderProposal(); draw();
  } finally { applying = false; if (proposal) renderProposal(); }
});

const stream = new EventSource(scope('/api/events'));
stream.addEventListener('revision', () => refresh().catch(report));
stream.onopen = () => { $('connection').textContent = '实时同步'; $('connection').className = 'online'; refresh().catch(report); };
stream.onerror = () => { $('connection').textContent = '重连中 · 草稿保留'; $('connection').className = ''; };
setInterval(() => { if (stream.readyState !== EventSource.OPEN && document.visibilityState === 'visible') refresh().catch(() => {}); }, 5000);
setupDrydocks({report, act: action => runAction(async () => {
  if (applying || reviewing) throw new Error('正在应用提案，请等待确认后再切换 Drydock');
  if (review?.proposal.status === 'pending' && (collectEdits().length || $('review-note').value.trim())) {
    throw new Error('审阅修改尚未提交，请先完成审阅或关闭提案。');
  }
  return action();
}, true), beforeNavigate: () => { chatEpoch++; chatController?.abort(); stream.close(); }});
await refresh();
const initial = Number(new URL(location.href).searchParams.get('ticket'));
const initialNote = new URL(location.href).searchParams.get('note');
const initialProposal = new URL(location.href).searchParams.get('proposal');
if (initialProposal) await selectProposal(initialProposal).catch(error => { report(error); renderForm(); });
else if (initialNote && graph.notes.some(n => n.id === initialNote)) selectNote(initialNote);
else if (initial && graph.tickets.some(t => t.id === initial)) await selectTicket(initial);
else renderForm();
