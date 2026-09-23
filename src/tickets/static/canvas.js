import {project} from './layout.js';
import {computeLayout, DEFAULT_LAYOUT} from './layout-strategies.js';
import {wrapText} from './text-layout.js';
import {noteKinds, notePreview, noteStates, relationNames} from './notes-data.js';
const NS = 'http://www.w3.org/2000/svg';
export const statuses = {open: '待处理', in_progress: '处理中', done: '已完成', closed: '已关闭'};
export const priorities = {low: '低', medium: '中', high: '高'};
function svg(tag, attrs = {}, text = null) {
  const element = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, value);
  if (text !== null) element.textContent = text;
  return element;
}
const measurer = document.createElement('canvas').getContext('2d');
function lines(text, width, maxLines = 2) {
  measurer.font = '14px "Segoe UI", "Microsoft YaHei", sans-serif';
  return wrapText(text, width, value => measurer.measureText(value).width, maxLines);
}

function geometryKey(node) {
  return JSON.stringify(node, (key, value) => key === 'data' ? undefined : value);
}
function refreshData(result, projection) {
  const data = new Map();
  const collect = node => { data.set(node.id, node.data); node.children?.forEach(collect); node.edges?.forEach(e => data.set(e.id, e.data)); };
  const update = node => { node.data = data.get(node.id); node.children?.forEach(update); node.edges?.forEach(e => { e.data = data.get(e.id); }); };
  collect(projection); update(result);
}

export class Diagram {
  constructor(element, onSelect, onCollapse, onEdge, {strategy = DEFAULT_LAYOUT, options = {}} = {}) {
    this.element = element; this.onSelect = onSelect; this.onCollapse = onCollapse; this.onEdge = onEdge;
    try { this.elk = new window.ELK({workerUrl: '/static/vendor/elk/elk-worker.min.js'}); }
    catch { this.elk = null; }
    this.view = null; this.layout = null; this.serial = 0; this.selected = null; this.drag = null;
    this.bounds = new Map();
    this.nodeBounds = new Map(); this.strategy = strategy; this.options = options; this.geometry = null;
    this.size = this.element.getBoundingClientRect();
    this.resizeObserver = new ResizeObserver(() => {
      const next = this.element.getBoundingClientRect(), previous = this.size;
      this.size = next;
      if (next.width === previous.width && next.height === previous.height) return;
      if (!this.view || !previous.width || !previous.height || !next.width || !next.height) return;
      const w = this.view.w * next.width / previous.width, h = this.view.h * next.height / previous.height;
      this.view = {x: this.view.x + (this.view.w - w) / 2, y: this.view.y + (this.view.h - h) / 2, w, h};
      const selected = this.bounds.get(String(this.selected));
      if (selected && (selected.x < this.view.x || selected.y < this.view.y ||
          selected.x + Math.min(selected.w, 320) > this.view.x + w ||
          selected.y + Math.min(selected.h, 112) > this.view.y + h)) this.reveal(this.selected);
      this.updateView();
    });
    this.resizeObserver.observe(this.element);
    this.element.addEventListener('wheel', event => { event.preventDefault(); this.zoom(event.deltaY > 0 ? 1.15 : 1 / 1.15, event); }, {passive: false});
    this.element.addEventListener('pointerdown', event => {
      if (event.target.closest('.node,.edge') || event.button !== 0) return;
      this.drag = {x: event.clientX, y: event.clientY, view: {...this.view}};
      this.element.setPointerCapture(event.pointerId);
    });
    this.element.addEventListener('pointermove', event => {
      if (!this.drag || !this.view) return;
      const rect = this.element.getBoundingClientRect();
      this.view = {...this.drag.view, x: this.drag.view.x - (event.clientX - this.drag.x) * this.view.w / rect.width,
        y: this.drag.view.y - (event.clientY - this.drag.y) * this.view.h / rect.height};
      this.updateView();
    });
    this.element.addEventListener('pointerup', () => { this.drag = null; });
    this.element.addEventListener('pointercancel', () => { this.drag = null; });
    this.element.addEventListener('keydown', event => {
      if (event.target !== this.element || !this.view) return;
      const movements = {ArrowLeft: [-40, 0], ArrowRight: [40, 0], ArrowUp: [0, -40], ArrowDown: [0, 40]};
      if (movements[event.key]) {
        event.preventDefault(); const [x, y] = movements[event.key];
        this.view.x += x; this.view.y += y; this.updateView();
      }
    });
  }
  updateView() {
    if (this.view) this.element.setAttribute('viewBox', `${this.view.x} ${this.view.y} ${this.view.w} ${this.view.h}`);
  }
  fit() {
    if (!this.layout) return;
    const rect = this.element.getBoundingClientRect(), aspect = rect.width / Math.max(1, rect.height);
    let w = (this.layout.width || 400) + 80, h = (this.layout.height || 240) + 80;
    if (w / h < aspect) w = h * aspect; else h = w / aspect;
    this.view = {x: -24, y: -24, w, h};
    this.updateView();
  }
  zoom(factor, event = null) {
    if (!this.view) return;
    const rect = this.element.getBoundingClientRect();
    const fx = event ? (event.clientX - rect.left) / rect.width : 0.5;
    const fy = event ? (event.clientY - rect.top) / rect.height : 0.5;
    if (this.view.w * factor < 120 || this.view.w * factor > 100000) return;
    const {x, y, w, h} = this.view;
    this.view = {x: x + w * fx * (1 - factor), y: y + h * fy * (1 - factor), w: w * factor, h: h * factor};
    this.updateView();
  }
  select(id) {
    const changed = this.selected !== id;
    this.selected = id;
    for (const node of this.element.querySelectorAll('[data-ticket]')) node.classList.toggle('selected', node.dataset.ticket === String(id));
    for (const node of this.element.querySelectorAll('[data-note]')) node.classList.toggle('selected', node.dataset.note === id);
    if (changed || (id != null && !this.bounds.has(String(id)))) {
      this.pendingReveal = !this.bounds.has(String(id)); this.reveal(id);
    }
  }
  reveal(id) {
    const box = this.bounds.get(String(id));
    if (!box) return;
    const rect = this.element.getBoundingClientRect(), aspect = rect.width / Math.max(1, rect.height);
    // Show the selected ticket at a readable scale; Fit remains the whole-graph overview.
    // Large parents reveal their readable header, not every descendant at miniature scale.
    const width = Math.min(box.w, 320), height = Math.min(box.h, 112);
    const w = Math.max(width + 48, (height + 48) * aspect, rect.width / 0.95);
    this.view = {x: box.x + width / 2 - w / 2, y: box.y + height / 2 - w / aspect / 2, w, h: w / aspect};
    this.updateView();
  }
  async render(graph, collapsed, filters) {
    const serial = ++this.serial, projected = project(graph, collapsed, filters);
    const status = document.querySelector('#layout-status');
    status.textContent = '正在排列…';
    document.querySelector('#empty-graph').hidden = projected.children.length > 0;
    let result;
    const geometry = `${this.strategy}:${JSON.stringify(this.options)}:${geometryKey(projected)}`;
    try {
      if (this.layout && geometry === this.geometry) {
        result = this.layout; refreshData(result, projected);
      } else result = await computeLayout(projected, {strategy: this.strategy, engine: this.elk, options: this.options});
    }
    catch (error) { if (serial === this.serial) status.textContent = '排列失败；数据仍保留，可使用列表视图。'; throw error; }
    if (serial !== this.serial) return;
    const anchor = this.anchor(graph);
    this.layout = result; this.geometry = geometry; status.textContent = '';
    this.element.dataset.layoutStrategy = this.strategy;
    const defs = svg('defs');
    for (const type of ['requires', 'assumes', 'supports', 'challenges', 'derived_from', 'option']) {
      const marker = svg('marker', {id: `arrow-${type}`, class: `marker-${type}`, viewBox: '0 0 12 12', refX: 11, refY: 6, markerWidth: 9, markerHeight: 9, orient: 'auto-start-reverse'});
      const shape = type === 'option' ? 'M 1 6 L 6 1 L 11 6 L 6 11 Z' : type === 'challenges' ? 'M 9 1 L 9 11 M 1 6 L 9 6' :
        ['assumes', 'derived_from'].includes(type) ? 'M 1 1 L 11 6 L 1 11' : 'M 1 1 L 11 6 L 1 11 Z';
      marker.append(svg('path', {d: shape})); defs.append(marker);
    }
    this.element.replaceChildren(defs);
    const backgroundLayer = svg('g'), edgesLayer = svg('g'), nodesLayer = svg('g');
    this.element.append(backgroundLayer, edgesLayer, nodesLayer);
    const positions = new Map([['root', {x: 0, y: 0}]]), allEdges = [];
    this.bounds.clear();
    this.nodeBounds.clear();
    const visit = (parent, ox = 0, oy = 0) => {
      for (const node of parent.children || []) {
        const x = ox + node.x, y = oy + node.y;
        positions.set(node.id, {x, y});
        this.nodeBounds.set(node.id, {x, y, w: node.width, h: node.height});
        if (node.data?.ticket) this.bounds.set(String(node.data.ticket.id), {x, y, w: node.width, h: node.height});
        if (node.data?.note) this.bounds.set(node.data.note.id, {x, y, w: node.width, h: node.height});
        this.drawNode(node, x, y, node.children?.length ? backgroundLayer : nodesLayer);
        visit(node, x, y);
      }
      for (const edge of parent.edges || []) allEdges.push({edge, ox, oy});
    };
    visit(result);
    for (const {edge, ox, oy} of allEdges) {
      const origin = edge.container ? positions.get(edge.container) || {x: ox, y: oy} : {x: ox, y: oy};
      const type = edge.data?.type || 'requires';
      const descriptions = edge.data?.edges?.map(e => type === 'requires' ? `#${e.blocker_id} → #${e.blocked_id}` : `${e.source} ${type === 'relates' ? '—' : '→'} ${e.target}`) || [];
      const group = svg('g', {class: `edge relation-${type} ${edge.data?.removed ? 'removed' : ''}`, tabindex: 0, role: 'button',
        'data-source': edge.sources[0], 'data-target': edge.targets[0], 'data-relation': type,
        'aria-label': `${relationNames[type]} ${descriptions.join('；')}`});
      group.append(svg('title', {}, `${relationNames[type]}\n${descriptions.join('\n')}`));
      for (const section of edge.sections || []) {
        const points = [section.startPoint, ...(section.bendPoints || []), section.endPoint];
        const d = points.map((p, i) => `${i ? 'L' : 'M'} ${p.x + origin.x} ${p.y + origin.y}`).join(' ');
        group.append(svg('path', {d, class: 'edge-hit'}), svg('path', {d, class: 'edge-path', ...(type === 'relates' ? {} : {'marker-end': `url(#arrow-${type})`})}));
      }
      for (const label of edge.labels || []) group.append(svg('text', {x: label.x + origin.x + 4, y: label.y + origin.y + 13}, label.text));
      const select = event => { event.stopPropagation(); this.onEdge(edge.data?.edges || []); };
      group.addEventListener('click', select);
      group.addEventListener('keydown', event => { if (event.key === 'Enter') select(event); });
      edgesLayer.append(group);
    }
    const nextAnchor = this.anchor(graph);
    if (this.frameLocal) {
      this.fit();
      if (this.element.getBoundingClientRect().width / this.view.w < .65) this.reveal(this.selected);
      this.frameLocal = false;
    }
    else if (!this.view) { this.fit(); this.reveal(this.selected); }
    else {
      if (this.pendingReveal) this.reveal(this.selected);
      else if (anchor && nextAnchor) { this.view.x += nextAnchor.x - anchor.x; this.view.y += nextAnchor.y - anchor.y; }
      this.updateView();
    }
    this.pendingReveal = false;
    // Refresh selection styles without treating a filtered/collapsed selection as navigation.
    for (const node of this.element.querySelectorAll('[data-ticket]')) node.classList.toggle('selected', node.dataset.ticket === String(this.selected));
    for (const node of this.element.querySelectorAll('[data-note]')) node.classList.toggle('selected', node.dataset.note === this.selected);
  }
  anchor(graph) {
    if (this.selected == null) return null;
    const direct = this.bounds.get(String(this.selected));
    if (direct) return direct;
    let ticket = graph.tickets.find(t => t.id === this.selected);
    const group = ticket?.group_name;
    while (ticket?.parent_id != null) {
      ticket = graph.tickets.find(t => t.id === ticket.parent_id);
      const box = ticket && this.bounds.get(String(ticket.id));
      if (box) return box;
    }
    return group === undefined ? null : this.nodeBounds.get(`g:${group}`);
  }
  drawNode(node, x, y, layer) {
    const data = node.data || {}, t = data.ticket, note = data.note, isGroup = !t && !note;
    const classes = ['node', isGroup ? 'group' : '', note ? 'note-node' : '', node.children?.length && t ? 'parent' : '', t?._proposal || note?._proposal || '',
      t && ['done', 'closed'].includes(t.status) ? 'terminal' : ''].filter(Boolean).join(' ');
    const group = svg('g', {class: classes, transform: `translate(${x},${y})`, tabindex: 0, role: 'button',
      'data-node-key': node.id, 'aria-label': t ? `工单 #${t.id} ${t.title} ${statuses[t.status]}` : note ? `${noteKinds[note.kind]} ${note.title}` : `工单组 ${data.label}`});
    if (t) group.dataset.ticket = t.id;
    if (note) group.dataset.note = note.id;
    group.append(svg('rect', {width: node.width, height: node.height}));
    if (isGroup) {
      group.append(svg('title', {}, data.label));
      group.append(svg('text', {x: 16, y: 29}, lines(data.label, node.width - 56, 1)[0] || ''));
    } else if (note) {
      group.append(svg('title', {}, `${note.title}\n${notePreview(note)}`));
      group.append(svg('text', {x: 14, y: 23, class: 'meta'}, `◇ ${noteKinds[note.kind]} · ${noteStates[note.kind]?.[note.state] || note.state}`));
      lines(note.title, 214).forEach((line, i) => group.append(svg('text', {x: 14, y: 48 + i * 19}, line)));
      const excerpt = note.abstract_zh ? lines(note.abstract_zh, 214, 1)[0] : '';
      group.append(svg('text', {x: 14, y: 95, class: 'meta'}, excerpt || 'Markdown 笔记'));
    } else {
      group.append(svg('title', {}, `#${t.id} ${t.title}`));
      group.append(svg('text', {x: 14, y: 23, class: 'meta'}, `#${t.id} · ${statuses[t.status]} · ${priorities[t.priority]}`));
      lines(t.title, Math.min(node.width - 30, 310)).forEach((line, i) => group.append(svg('text', {x: 14, y: 48 + i * 19}, line)));
      const info = t._proposal === 'deleted' ? '拟删除' : t._proposal ? '修改预览' : (t.blockers?.length ? `等待 ${t.blockers.map(id => '#' + id).join('、')}` : t.review_notes?.length ? '前提待复核' : t.assumptions?.length ? '依赖已采纳的假设' : t.ready ? '无执行阻塞' : '');
      group.append(svg('text', {x: 14, y: 95, class: `meta ${t.blockers?.length ? 'blocked' : 'ready'}`}, info.length > 34 ? info.slice(0, 32) + '…' : info));
    }
    if (data.hasChildren) {
      const control = svg('text', {x: node.width - 27, y: isGroup ? 29 : 24, class: 'collapse-control', tabindex: 0, role: 'button',
        'aria-label': `${data.collapsed ? '展开' : '收起'}${t ? '#' + t.id : data.label}`}, data.collapsed ? '+' : '−');
      const toggle = event => { event.stopPropagation(); this.onCollapse(node.id); };
      control.addEventListener('click', toggle); control.addEventListener('keydown', event => { if (['Enter', ' '].includes(event.key)) { event.preventDefault(); toggle(event); } });
      group.append(control);
    }
    const select = event => { event.stopPropagation(); if (t || note) this.onSelect(t?.id || note.id); else if (!data.notebook) this.onCollapse(node.id); };
    group.addEventListener('click', select); group.addEventListener('keydown', event => { if (['Enter', ' '].includes(event.key)) { event.preventDefault(); select(event); } });
    if (t || note) {
      const highlight = () => this.highlight(node.id);
      const clear = () => this.highlight(null);
      group.addEventListener('pointerenter', highlight); group.addEventListener('pointerleave', clear);
      group.addEventListener('focus', highlight); group.addEventListener('blur', clear);
    }
    layer.append(group);
  }
  highlight(key) {
    const neighbors = new Set([key]);
    for (const edge of this.element.querySelectorAll('.edge')) {
      const connected = !key || edge.dataset.source === key || edge.dataset.target === key;
      edge.classList.toggle('dimmed', !connected);
      if (connected) { neighbors.add(edge.dataset.source); neighbors.add(edge.dataset.target); }
    }
    for (const node of this.element.querySelectorAll('.node:not(.group):not(.parent)')) node.classList.toggle('dimmed', !!key && !neighbors.has(node.dataset.nodeKey));
  }
}
