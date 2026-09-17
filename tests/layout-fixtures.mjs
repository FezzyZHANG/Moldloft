// Written synthetic data only; never load the operator's graph for layout checks.
export const ticket = (id, parent_id = null, group_name = 'Fixture') => ({id, parent_id, group_name,
  title: `Review dependency contract ${id}`, description: 'Synthetic layout fixture.', tech_notes: '',
  status: 'open', priority: 'medium', version: 1, blockers: [], ready: true});
export function mixedFixture() {
  const tickets = [];
  for (let g = 0; g < 7; g++) {
    const first = g * 4 + 1, group = `Theme ${g + 1}`;
    tickets.push(ticket(first, null, group), ticket(first + 1, first, group),
      ticket(first + 2, first + 1, group), ticket(first + 3, null, group));
  }
  const edges = [];
  for (let g = 0; g < 5; g++) edges.push({blocker_id: g * 4 + 2, blocked_id: g * 4 + 4});
  edges.push({blocker_id: 4, blocked_id: 8}, {blocker_id: 8, blocked_id: 12});
  const notes = Array.from({length: 10}, (_, i) => ({id: `n-fixture-${i}`, title: `Explore uncertain direction ${i}`,
    kind: ['assumption', 'evidence', 'idea', 'question'][i % 4], state: ['proposed', 'recorded', 'open', 'open'][i % 4],
    abstract_zh: '先用小实验检验假设，保留尚未验证的方向。', abstract_en: 'Test the premise before adopting a candidate direction.',
    body: 'Synthetic evidence is not a claim about real work.', relations: [], version: `fixture-${i}`}));
  const relations = [
    [0, 'ticket:2', 'assumes'], [1, 'note:n-fixture-0', 'supports'], [2, 'note:n-fixture-3', 'option'],
    [3, 'note:n-fixture-0', 'relates'], [0, 'note:n-fixture-2', 'relates'], [1, 'ticket:4', 'relates'],
    [2, 'ticket:6', 'option'], [4, 'ticket:14', 'assumes'], [5, 'note:n-fixture-4', 'supports'],
    [6, 'ticket:18', 'option'], [7, 'note:n-fixture-6', 'relates'],
  ].map(([i, target, type]) => ({owner: notes[i].id, source: `note:${notes[i].id}`, target, type}));
  for (const note of notes) note.relations = relations.filter(r => r.owner === note.id).map(({type, target}) => ({type, target}));
  return {revision: 1, notes_revision: 'fixture', tickets, notes, edges, relations, links: [], notes_errors: []};
}
export function largeFixture() {
  const tickets = Array.from({length: 500}, (_, i) => {
    const first = Math.floor(i / 50) * 50 + 1;
    return ticket(i + 1, i + 1 === first ? null : first, `Group ${Math.floor(i / 50)}`);
  });
  const pairs = new Set(); let seed = 19;
  while (pairs.size < 1000) {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    const a = seed % 499 + 1;
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
    pairs.add(`${a},${a + 1 + seed % (500 - a)}`);
  }
  return {tickets, edges: [...pairs].map(pair => {
    const [blocker_id, blocked_id] = pair.split(',').map(Number); return {blocker_id, blocked_id};
  }), notes: [], relations: []};
}
export const fixtures = {
  mixed: mixedFixture,
  disconnected: () => ({tickets: Array.from({length: 14}, (_, i) => ticket(i + 1, null, `Theme ${i % 7}`)), edges: []}),
  chain: () => ({tickets: Array.from({length: 12}, (_, i) => ticket(i + 1)),
    edges: Array.from({length: 11}, (_, i) => ({blocker_id: i + 1, blocked_id: i + 2}))}),
  dense: () => { const g = mixedFixture(); g.tickets = g.tickets.slice(0, 4); g.edges = g.edges.slice(0, 1);
    g.notes = g.notes.slice(0, 8); g.relations = g.relations.slice(0, 6);
    for (const source of g.notes.slice(4)) for (const target of g.notes.slice(0, 4)) g.relations.push({
      source: `note:${source.id}`, target: `note:${target.id}`, type: 'relates', owner: source.id});
    return g; },
};
export const flatten = graph => [graph, ...(graph.children || []).flatMap(flatten)];
export function observations(layout) {
  const boxes = [], segments = [], labels = [], endpoints = [], positions = new Map();
  function visit(node, ox = 0, oy = 0, ancestors = []) {
    const x = ox + (node.x || 0), y = oy + (node.y || 0);
    positions.set(node.id, {x, y});
    if (node.id !== 'root') boxes.push({id: node.id, x, y, w: node.width, h: node.height, ancestors});
    for (const child of node.children || []) visit(child, x, y, [...ancestors, node.id]);
  }
  visit(layout);
  for (const node of flatten(layout)) for (const edge of node.edges || []) {
    const origin = positions.get(edge.container || node.id);
    for (const section of edge.sections || []) {
      const points = [section.startPoint, ...(section.bendPoints || []), section.endPoint].map(p => ({x: p.x + origin.x, y: p.y + origin.y}));
      endpoints.push({...points[0], node: edge.sources[0]}, {...points.at(-1), node: edge.targets[0]});
      for (let i = 1; i < points.length; i++) segments.push({id: edge.id, a: points[i - 1], b: points[i]});
    }
    for (const label of edge.labels || []) labels.push({id: label.id, x: label.x + origin.x, y: label.y + origin.y, w: label.width, h: label.height});
  }
  const overlap = (a, b) => a.x < b.x + b.w - .1 && b.x < a.x + a.w - .1 && a.y < b.y + b.h - .1 && b.y < a.y + a.h - .1;
  let overlaps = 0, crossings = 0;
  for (let i = 0; i < boxes.length; i++) for (const b of boxes.slice(i + 1)) {
    const a = boxes[i]; if (!a.ancestors.includes(b.id) && !b.ancestors.includes(a.id) && overlap(a, b)) overlaps++;
  }
  for (let i = 0; i < segments.length; i++) for (const b of segments.slice(i + 1)) {
    const a = segments[i]; if (a.id === b.id) continue;
    const h = a.a.y === a.b.y ? a : b, v = h === a ? b : a;
    if (h.a.y !== h.b.y || v.a.x !== v.b.x) continue;
    if (v.a.x > Math.min(h.a.x, h.b.x) && v.a.x < Math.max(h.a.x, h.b.x) &&
        h.a.y > Math.min(v.a.y, v.b.y) && h.a.y < Math.max(v.a.y, v.b.y)) crossings++;
  }
  const leaves = boxes.filter(b => !boxes.some(c => c.ancestors.includes(b.id)));
  return {width: layout.width, height: layout.height, area: layout.width * layout.height,
    overlaps, crossings, labelNodeOverlaps: labels.filter(l => leaves.some(b => overlap(l, b))).length,
    obscuredEndpoints: endpoints.filter(p => leaves.some(b => b.id !== p.node && p.x > b.x + .1 && p.x < b.x + b.w - .1 && p.y > b.y + .1 && p.y < b.y + b.h - .1)).length};
}
