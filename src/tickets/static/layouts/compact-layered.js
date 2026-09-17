// Compound execution containers stay intact. Independent components may be packed without
// changing any edge route, since there are no edges between these components.
function components(graph) {
  const owner = new Map(), parents = new Map(graph.children.map(n => [n.id, n.id]));
  const visit = (node, root) => { owner.set(node.id, root); node.children?.forEach(n => visit(n, root)); };
  graph.children.forEach(n => visit(n, n.id));
  const root = id => { while (parents.get(id) !== id) id = parents.get(id); return id; };
  for (const edge of graph.edges) {
    const a = root(owner.get(edge.sources[0])), b = root(owner.get(edge.targets[0]));
    if (a !== b) parents.set(b, a);
  }
  const groups = new Map();
  for (const node of graph.children) {
    const id = root(node.id);
    if (!groups.has(id)) groups.set(id, {id: 'root', children: [], edges: []});
    groups.get(id).children.push(node);
  }
  for (const edge of graph.edges) groups.get(root(owner.get(edge.sources[0]))).edges.push(edge);
  return [...groups.values()];
}

// Deterministic first-fit shelves keep the upper-left occupied; long chains remain wide.
function pack(parts, aspect, gap) {
  const area = parts.reduce((sum, p) => sum + (p.width + gap) * (p.height + gap), 0);
  const target = Math.max(...parts.map(p => p.width), Math.sqrt(area * aspect));
  const rows = [];
  for (const part of [...parts].sort((a, b) => b.height - a.height || b.width - a.width || a.order - b.order)) {
    let row = rows.find(r => r.used + gap + part.width <= target);
    if (!row) { row = {y: rows.reduce((h, r) => h + r.height + gap, 0), height: part.height, used: 0}; rows.push(row); }
    part.x = row.used ? row.used + gap : 0; part.y = row.y;
    row.used = part.x + part.width;
  }
}

export const compactLayered = {
  label: 'Compact layered', defaults: {aspect: 2.2, gap: 24},
  async compute(graph, {engine, options}) {
    if (!engine) throw new Error('Layout worker unavailable');
    if (!Number.isFinite(options.aspect) || options.aspect <= 0 || !Number.isFinite(options.gap) || options.gap < 16) throw new Error('Invalid compact layout options');
    if (!graph.children.length) return {...graph, width: 0, height: 0};
    const layoutOptions = {
      'elk.algorithm': 'layered', 'elk.direction': 'RIGHT', 'elk.hierarchyHandling': 'INCLUDE_CHILDREN',
      'elk.edgeRouting': 'ORTHOGONAL', 'elk.randomSeed': '1', 'elk.aspectRatio': String(options.aspect),
      'elk.padding': '[top=8,left=8,bottom=8,right=8]',
      'elk.layered.spacing.nodeNodeBetweenLayers': '32', 'elk.spacing.nodeNode': '24',
      'elk.spacing.edgeNode': '18', 'elk.layered.spacing.edgeNodeBetweenLayers': '18',
      'elk.spacing.edgeEdge': '12', 'elk.layered.spacing.edgeEdgeBetweenLayers': '12',
      'elk.layered.compaction.connectedComponents': 'true',
    };
    function configure(node) {
      if (node.children?.length) {
        node.layoutOptions = {...layoutOptions, 'elk.padding': node.data?.ticket ?
          '[top=112,left=20,bottom=20,right=20]' : '[top=44,left=20,bottom=20,right=20]'};
        node.children.forEach(configure);
      }
    }
    graph.children.forEach(configure);
    for (const edge of graph.edges) for (const label of edge.labels || []) label.layoutOptions = {'elk.edgeLabels.placement': 'CENTER'};
    const parts = [];
    // Serialize requests to the single worker; all expensive crossing/routing work is off-thread.
    for (const part of components(graph)) parts.push({...await engine.layout({...part, layoutOptions}), order: parts.length});
    pack(parts, options.aspect, options.gap);
    const result = {...graph, children: [], edges: [], width: 0, height: 0};
    for (const part of parts) {
      result.width = Math.max(result.width, part.x + part.width);
      result.height = Math.max(result.height, part.y + part.height);
      for (const node of part.children) result.children.push({...node, x: node.x + part.x, y: node.y + part.y});
      for (const edge of part.edges || []) {
        // Root-relative routes move with their component. Nested routes remain local.
        if (!edge.container || edge.container === 'root') {
          const move = p => ({...p, x: p.x + part.x, y: p.y + part.y});
          edge.sections = edge.sections?.map(s => ({...s, startPoint: move(s.startPoint), endPoint: move(s.endPoint), bendPoints: s.bendPoints?.map(move)}));
          edge.labels = edge.labels?.map(move);
        }
        result.edges.push(edge);
      }
    }
    // The in-process test worker exposes GWT identity hashes; they are not geometry.
    const clean = node => { delete node.$H; node.children?.forEach(clean); };
    clean(result);
    return result;
  },
};
