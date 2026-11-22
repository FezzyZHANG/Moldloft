// Split by top-level ownership so compound nodes are never split between shelves.
function components(graph) {
  const owner = new Map();
  const parent = new Map(graph.children.map(node => [node.id, node.id]));
  function visit(node, root) {
    owner.set(node.id, root);
    node.children?.forEach(child => visit(child, root));
  }
  const rootOf = id => {
    while (parent.get(id) !== id) id = parent.get(id);
    return id;
  };
  graph.children.forEach(node => visit(node, node.id));
  for (const edge of graph.edges) {
    const a = rootOf(owner.get(edge.sources[0]));
    const b = rootOf(owner.get(edge.targets[0]));
    if (a !== b) parent.set(b, a);
  }
  const parts = new Map();
  for (const child of graph.children) {
    const key = rootOf(child.id);
    if (!parts.has(key)) parts.set(key, {id: graph.id, children: [], edges: []});
    parts.get(key).children.push(child);
  }
  for (const edge of graph.edges) parts.get(rootOf(owner.get(edge.sources[0]))).edges.push(edge);
  return [...parts.values()];
}

function pack(parts, aspect, gap) {
  const area = parts.reduce((sum, p) => sum + (p.width + gap) * (p.height + gap), 0);
  const target = Math.max(Math.sqrt(area * aspect), ...parts.map(p => p.width));
  const shelves = [];
  let y = 0;
  for (const part of [...parts].sort((a, b) => b.height - a.height || b.width - a.width)) {
    let shelf = shelves.find(row => row.used + gap + part.width <= target);
    if (!shelf) {
      shelf = {y, used: 0};
      shelves.push(shelf);
      y += part.height + gap;
    }
    part.x = shelf.used ? shelf.used + gap : 0;
    part.y = shelf.y;
    shelf.used = part.x + part.width;
  }
}

export async function computeLayout(projection, {engine, options = {}} = {}) {
  if (!engine) throw new Error('ELK layout worker unavailable');
  const {aspect = 2.2, gap = 24} = options;
  if (!Number.isFinite(aspect) || aspect <= 0 || !Number.isFinite(gap) || gap < 0) {
    throw new Error('Invalid layout spacing');
  }
  const layoutOptions = {
    'elk.algorithm': 'layered',
    'elk.hierarchyHandling': 'INCLUDE_CHILDREN',
    'elk.direction': 'RIGHT',
    'elk.edgeRouting': 'ORTHOGONAL',
    'elk.randomSeed': '1',
    'elk.aspectRatio': String(aspect),
    'elk.padding': '[top=8,left=8,bottom=8,right=8]',
    'elk.layered.spacing.nodeNodeBetweenLayers': '36',
    'elk.spacing.nodeNode': '24',
  };
  const data = new Map();
  function configure({data: metadata, children, ...node}) {
    data.set(node.id, metadata);
    if (!children?.length) return node;
    return {...node, children: children.map(configure), layoutOptions: {
      ...layoutOptions,
      'elk.padding': metadata?.ticket ? '[top=122,left=18,bottom=18,right=18]' :
        '[top=50,left=18,bottom=18,right=18]',
    }};
  }
  const parts = [];
  for (const part of components(projection)) {
    const children = part.children.map(configure);
    const edges = part.edges.map(({data: metadata, labels, ...edge}) => {
      data.set(edge.id, metadata);
      return {...edge, labels: labels?.map(label => ({...label, layoutOptions: {'elk.edgeLabels.placement': 'CENTER'}}))};
    });
    parts.push(await engine.layout({id: part.id, children, edges, layoutOptions}));
  }
  pack(parts, aspect, gap);
  const result = {id: projection.id, children: [], edges: [], width: 0, height: 0};
  function restore(node) {
    delete node.$H;
    node.data = data.get(node.id);
    node.children?.forEach(restore);
  }
  for (const part of parts) {
    result.width = Math.max(result.width, part.x + part.width);
    result.height = Math.max(result.height, part.y + part.height);
    for (const node of part.children) {
      restore(node);
      result.children.push({...node, x: node.x + part.x, y: node.y + part.y});
    }
    const move = point => ({...point, x: point.x + part.x, y: point.y + part.y});
    for (const edge of part.edges || []) {
      delete edge.$H;
      if (!edge.container || edge.container === projection.id) {
        edge.sections = edge.sections?.map(section => ({...section,
          startPoint: move(section.startPoint), endPoint: move(section.endPoint),
          bendPoints: section.bendPoints?.map(move),
        }));
        if (edge.labels) edge.labels = edge.labels.map(move);
      }
      result.edges.push({...edge, data: data.get(edge.id)});
    }
  }
  return result;
}
