// Graph projection never changes the persisted items or dependency semantics.
export const ticketKey = id => `t:${id}`;
export const groupKey = name => `g:${name}`;
export const noteKey = id => `n:${id}`;
export const itemKey = (type, id) => type === 'ticket' ? ticketKey(id) : noteKey(id);
export const edgeTypes = ['requires', 'assumes', 'supports', 'challenges', 'relates'];
const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const edgeId = row => [row.blocker_id, row.blocked_id];
const linkId = row => [row.source_note_id, row.relation, row.target_type, row.target_id];

function relations(graph) {
  return [
    ...graph.edges.map(row => [ticketKey(row.blocker_id), ticketKey(row.blocked_id), 'requires', row]),
    ...(graph.note_links || []).map(row => [
      noteKey(row.source_note_id), itemKey(row.target_type, row.target_id), row.relation, row,
    ]),
  ];
}

export function neighborhood(graph, key, depth = 1, types = null, direction = 'both') {
  const items = new Set([
    ...graph.tickets.map(row => ticketKey(row.id)),
    ...(graph.notes || []).filter(row => row.in_graph !== false).map(row => noteKey(row.id)),
  ]);
  if (!items.has(key)) return new Set();
  const upstream = new Map();
  const downstream = new Map();
  function add(index, source, target) {
    if (!index.has(source)) index.set(source, []);
    index.get(source).push(target);
  }
  for (const [a, b, type] of relations(graph)) {
    if (!items.has(a) || !items.has(b) || (types && !types.has(type))) continue;
    add(downstream, a, b);
    add(upstream, b, a);
    if (type === 'relates') {
      add(downstream, b, a);
      add(upstream, a, b);
    }
  }
  function traverse(index) {
    const found = new Set([key]);
    let frontier = [key];
    for (let i = 0; i < Math.min(3, Math.max(1, depth)) && frontier.length; i++) {
      const next = [];
      for (const node of frontier) {
        for (const neighbor of index.get(node) || []) {
          if (found.has(neighbor)) continue;
          found.add(neighbor);
          next.push(neighbor);
        }
      }
      frontier = next;
    }
    return found;
  }
  if (direction === 'upstream') return traverse(upstream);
  if (direction === 'downstream') return traverse(downstream);
  return new Set([...traverse(upstream), ...traverse(downstream)]);
}

export function matchesItem(row, search) {
  const query = search?.trim().toLowerCase();
  if (!query) return true;
  const id = typeof row.id === 'number' ? `#${row.id}` : row.id;
  return [id, row.title, row.description, row.tech_notes, row.summary, row.body, row.group_name]
    .some(value => value?.toLowerCase().includes(query));
}

export function matchesStatus(row, status) {
  if (status === 'active') return row.status === 'open' || row.status === 'in_progress';
  if (status === 'ready') return Boolean(row.ready);
  return !status || row.status === status;
}

export function groupValue(name) {
  if (!name) return '__ungrouped__';
  return name.startsWith('__') ? '_' + name : name;
}

export function filterItems(graph, filter, local = null) {
  const byId = new Map(graph.tickets.map(row => [row.id, row]));
  const matching = graph.tickets.filter(row => (!local || local.has(ticketKey(row.id))) &&
    matchesStatus(row, filter.status) && (!filter.group || groupValue(row.group_name) === filter.group) &&
    matchesItem(row, filter.search));
  const included = new Set();
  for (const row of matching) {
    let parent = row;
    while (parent && !included.has(parent.id)) {
      included.add(parent.id);
      parent = byId.get(parent.parent_id);
    }
  }
  const scoped = !filter.local && !local && (filter.status || filter.group);
  const linked = scoped && new Set((graph.note_links || [])
    .filter(link => link.target_type === 'ticket' && included.has(link.target_id)).map(link => link.source_note_id));
  const notes = (graph.notes || []).filter(note => matchesItem(note, filter.search) &&
    (!local || local.has(noteKey(note.id))) && (!linked || note.in_graph === false || linked.has(note.id)));
  return {matching, included, notes};
}

export function project(graph, collapsed = new Set(), filter = {}) {
  const showNotes = filter.showNotes !== false;
  const types = showNotes ? filter.types : new Set(filter.types && !filter.types.has('requires') ? [] : ['requires']);
  const local = filter.focus ? neighborhood(graph, filter.focus, filter.depth, types, filter.direction) : null;
  const {included, notes} = filterItems(graph, filter, local);
  const groups = new Map();
  const tickets = new Map();
  for (const ticket of graph.tickets.filter(row => included.has(row.id)).sort((a, b) => compare(a.id, b.id))) {
    const id = ticket.id;
    const group = ticket.group_name || '';
    if (!groups.has(group)) {
      groups.set(group, {id: groupKey(group), width: 252, height: 60, children: [],
        data: {group, label: group || '未分组'}});
    }
    tickets.set(id, {id: ticketKey(id), width: 244, height: 114, children: [], data: {ticket}});
  }
  for (const node of tickets.values()) {
    const row = node.data.ticket;
    const parent = tickets.get(row.parent_id) || groups.get(row.group_name || '');
    parent.children.push(node);
  }
  const visible = new Map();
  const owner = new Map();
  function visit(node, hiddenBy = null) {
    const representative = hiddenBy || node.id;
    owner.set(node.id, representative);
    if (!hiddenBy) visible.set(node.id, node);
    const children = node.children || [];
    node.data.hasChildren = children.length > 0;
    node.data.collapsed = node.data.hasChildren && collapsed.has(node.id);
    for (const child of children) visit(child, hiddenBy || (node.data.collapsed ? node.id : null));
    if (!children.length || node.data.collapsed) delete node.children;
  }

  const children = [...groups.values()].sort((a, b) => compare(a.id, b.id));
  for (const group of children) visit(group);
  if (showNotes) {
    for (const note of notes.filter(row => row.in_graph !== false).sort((a, b) => compare(a.id, b.id))) {
      const node = {id: noteKey(note.id), width: 244, height: 114, data: {note}};
      children.push(node);
      visit(node);
    }
  }
  for (const result of filter.drift?.values() || []) {
    if (result.status !== 'drifted' && result.status !== 'missing') continue;
    const node = visible.get(owner.get(itemKey(result.target_type, result.target_id)));
    if (node) {
      const counts = node.data.drift ??= {drifted: 0, missing: 0};
      counts[result.status]++;
    }
  }

  const bundles = new Map();
  for (const [from, to, type, row] of relations(graph)) {
    let source = owner.get(from);
    let target = owner.get(to);
    if (!source || !target || source === target || (types && !types.has(type))) continue;
    if (type === 'relates' && source > target) [source, target] = [target, source];
    const change = row._proposal || '';
    const id = JSON.stringify([source, target, type, change]);
    if (!bundles.has(id)) {
      bundles.set(id, {id, sources: [source], targets: [target], data: {type, change, edges: []}});
    }
    bundles.get(id).data.edges.push(row);
  }
  const edges = [...bundles.values()].sort((a, b) => compare(a.id, b.id));
  for (const edge of edges) {
    const rows = edge.data.edges;
    const identity = edge.data.type === 'requires' ? edgeId : linkId;
    rows.sort((a, b) => compare(JSON.stringify(identity(a)), JSON.stringify(identity(b))));
    if (rows.length > 1) {
      const text = `×${rows.length}`;
      edge.labels = [{id: `${edge.id}:label`, text, width: 8 * text.length + 6, height: 16}];
    }
  }
  return {id: 'root', children, edges};
}

export function previewOverlay(base, inspection) {
  if (!inspection?.preview) return base;
  const graph = {...base, ...inspection.preview};
  const marks = new Map(inspection.changes.map(row => [JSON.stringify([row.entity, row.id]), row.action]));
  for (const [table, identity] of Object.entries({
    tickets: row => row.id, notes: row => row.id, edges: edgeId, note_links: linkId,
  })) {
    const links = table === 'edges' || table === 'note_links';
    const mark = row => marks.get(JSON.stringify([table, identity(row)]));
    graph[table] = (graph[table] || []).map(row => {
      const action = mark(row);
      return action ? {...row, _proposal: action === 'create' ? 'added' : 'changed'} : row;
    });
    for (const row of base[table] || []) {
      if (mark(row) === 'delete') graph[table].push({...row, _proposal: links ? 'removed' : 'deleted'});
    }
  }
  return graph;
}
