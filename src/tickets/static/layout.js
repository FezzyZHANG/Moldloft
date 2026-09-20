// Projection is presentation only. Dependency validation always uses the full graph.
import {matchesNote, referenceKey, relationLabels} from './notes-data.js';
export const ticketKey = id => `t:${id}`;
export const groupKey = name => `g:${name}`;
export const noteKey = id => `n:${id}`;
// Saved references have numeric IDs; reviewed previews can target new: aliases.
const ticketRefId = ref => ref.startsWith('ticket:new:') ? ref.slice(7) : Number(ref.slice(7));

export function neighborhood(graph, key, depth = 1, types = null, direction = 'both') {
  const pairs = [
    ...(types && !types.has('requires') ? [] : graph.edges.map(e => [ticketKey(e.blocker_id), ticketKey(e.blocked_id)])),
    ...(graph.relations || []).filter(r => !r.missing && (!types || types.has(r.type))).map(r => [referenceKey(r.source), referenceKey(r.target), r.type === 'relates']),
  ];
  function traverse(upstream) {
    const found = new Set([key]); let frontier = new Set([key]);
    for (let i = 0; i < Math.min(3, Math.max(1, depth)); i++) {
      const next = new Set();
      for (const [a, b, undirected] of pairs) {
        if ((!upstream || undirected) && frontier.has(a) && !found.has(b)) next.add(b);
        if ((upstream || undirected) && frontier.has(b) && !found.has(a)) next.add(a);
      }
      next.forEach(k => found.add(k)); frontier = next;
    }
    return found;
  }
  // Keep each direction's frontier separate so a shared neighbor cannot introduce a side branch.
  if (direction === 'upstream') return traverse(true);
  if (direction === 'downstream') return traverse(false);
  return new Set([...traverse(true), ...traverse(false)]);
}

export function project(graph, collapsed = new Set(), filter = {}) {
  const byId = new Map(graph.tickets.map(t => [t.id, t]));
  const included = new Set();
  const local = filter.focus ? neighborhood(graph, filter.focus, filter.depth, filter.types, filter.direction) : null;
  for (const t of graph.tickets) {
    if (local && !local.has(ticketKey(t.id))) continue;
    if (filter.group !== undefined && t.group_name !== filter.group) continue;
    if (filter.status && t.status !== filter.status) continue;
    if (filter.search && !`${t.id} ${t.title} ${t.title_zh || ''}`.toLowerCase().includes(filter.search.toLowerCase())) continue;
    let cursor = t;
    while (cursor && !included.has(cursor.id)) { included.add(cursor.id); cursor = byId.get(cursor.parent_id); }
  }
  const containers = new Map(), nodes = new Map();
  const groups = [...new Set([...included].map(id => byId.get(id).group_name))].sort();
  for (const group of groups) containers.set(groupKey(group), {
    id: groupKey(group), width: 268, height: 72, children: [],
    data: {group, label: group || '未分组'},
  });
  for (const id of included) {
    const ticket = byId.get(id);
    nodes.set(ticketKey(id), {id: ticketKey(id), width: 244, height: 112, children: [],
      data: {ticket}});
  }
  const parentKey = ticket => ticket.parent_id !== null && included.has(ticket.parent_id) ? ticketKey(ticket.parent_id) : groupKey(ticket.group_name);
  for (const node of nodes.values()) {
    const parent = nodes.get(parentKey(node.data.ticket)) || containers.get(parentKey(node.data.ticket));
    parent.children.push(node);
  }
  function trim(node) {
    node.data.hasChildren = node.children.length > 0;
    node.data.collapsed = collapsed.has(node.id);
    if (node.data.collapsed) node.children = [];
    else node.children.sort((a, b) => String(a.id).localeCompare(String(b.id), 'en', {numeric: true})).forEach(trim);
    if (!node.children.length) delete node.children;
  }
  for (const node of containers.values()) trim(node);
  const visibleNodes = new Map();
  function collect(node) { visibleNodes.set(node.id, node); node.children?.forEach(collect); }
  containers.forEach(collect);
  function representative(id) {
    let t = byId.get(id);
    if (!t || !included.has(id)) return null;
    while (t) {
      const key = ticketKey(t.id);
      if (visibleNodes.has(key)) return key;
      t = byId.get(t.parent_id);
    }
    return groupKey(byId.get(id).group_name);
  }
  const aggregate = new Map();
  const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;
  const sortedEdges = [...graph.edges].sort((a, b) => a.blocker_id - b.blocker_id || a.blocked_id - b.blocked_id || Number(!!a._removed) - Number(!!b._removed));
  for (const edge of filter.types && !filter.types.has('requires') ? [] : sortedEdges) {
    const source = representative(edge.blocker_id), target = representative(edge.blocked_id);
    if (!source || !target || source === target) continue;
    const key = JSON.stringify([source, target, !!edge._removed]);
    if (!aggregate.has(key)) aggregate.set(key, {id: `e:${aggregate.size}`, sources: [source], targets: [target], data: {type: 'requires', edges: [], removed: !!edge._removed}});
    aggregate.get(key).data.edges.push(edge);
  }
  const notes = filter.showNotes === false ? [] : (graph.notes || []).filter(n => {
    if (local && !local.has(noteKey(n.id))) return false;
    if (filter.search && !matchesNote(n, filter.search)) return false;
    if (filter.group !== undefined || filter.status) {
      return (graph.relations || []).some(r => r.owner === n.id && r.target.startsWith('ticket:') && included.has(ticketRefId(r.target)));
    }
    return true;
  });
  // Notes are independent entities, not a containment group. The sidebar owns the notebook.
  const noteNodes = notes.sort((a, b) => compare(a.id, b.id)).map(note => ({id: noteKey(note.id), width: 244, height: 112, data: {note}}));
  const noteIds = new Set(notes.map(n => n.id));
  const endpoint = ref => ref.startsWith('ticket:') ? representative(ticketRefId(ref)) : noteIds.has(ref.slice(5)) ? noteKey(ref.slice(5)) : null;
  const relations = [...(graph.relations || [])].sort((a, b) => compare(JSON.stringify([a.source, a.target, a.type]), JSON.stringify([b.source, b.target, b.type])));
  for (const relation of relations) {
    if (relation.missing || (filter.types && !filter.types.has(relation.type))) continue;
    const source = endpoint(relation.source), target = endpoint(relation.target);
    if (!source || !target || source === target) continue;
    const key = JSON.stringify([source, target, relation.type]);
    if (!aggregate.has(key)) aggregate.set(key, {id: `e:${aggregate.size}`, sources: [source], targets: [target], data: {type: relation.type, edges: []}});
    aggregate.get(key).data.edges.push(relation);
  }
  for (const edge of aggregate.values()) {
    const count = edge.data.edges.length, type = edge.data.type;
    const text = `${type === 'requires' ? '' : relationLabels[type]}${count > 1 ? ' ×' + count : ''}`;
    if (text) edge.labels = [{id: `${edge.id}:label`, text, width: [...text].reduce((w, c) => w + (c.charCodeAt(0) > 255 ? 12 : 8), 8), height: 18}];
  }
  return {id: 'root', children: [...containers.values(), ...noteNodes], edges: [...aggregate.values()]};
}

export function previewOverlay(base, proposal) {
  const before = new Map(base.tickets.map(t => [t.id, t]));
  const after = new Map(proposal.preview.tickets.map(t => [t.id, t]));
  const changed = new Set(proposal.changes.filter(c => c.ticket_id !== undefined).map(c => c.ticket_id));
  const tickets = [...after.values()].map(t => ({...t, _proposal: changed.has(t.id) ? 'changed' : ''}));
  const changedNotes = new Set(proposal.changes.filter(c => c.kind === 'note').map(c => c.note_id));
  const notes = (proposal.preview.notes || []).map(n => ({...n, _proposal: changedNotes.has(n.id) ? 'changed' : ''}));
  for (const t of before.values()) if (!after.has(t.id)) tickets.push({...t, _proposal: 'deleted'});
  const key = e => `${e.blocker_id}>${e.blocked_id}`;
  const newEdges = new Set(proposal.preview.edges.map(key));
  const edges = [...proposal.preview.edges];
  for (const edge of base.edges) if (!newEdges.has(key(edge))) edges.push({...edge, _removed: true});
  return {...proposal.preview, tickets, notes, edges};
}
