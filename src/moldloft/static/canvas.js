import {computeLayout} from './compact-layered.js';
import {byId, relationLabels} from './ui.js';
import {wrapText} from './text-layout.js';

const svg = (tag, attributes = {}, text) => {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
};

function geometryKey(projection) {
  const shape = node => [node.id, node.width, node.height, node.children?.map(shape)];
  return JSON.stringify([projection.children.map(shape), projection.edges.map(edge => [
    edge.id, edge.sources, edge.targets, edge.labels?.map(label => [label.width, label.height]),
  ])]);
}

export class Canvas {
  constructor(actions) {
    this.actions = actions;
    this.svg = byId('dag');
    this.frame = byId('canvas-frame');
    this.empty = byId('canvas-empty');
    this.layer = svg('g');
    const defs = svg('defs');
    for (const type of Object.keys(relationLabels).filter(type => type !== 'relates')) {
      const marker = svg('marker', {id: `arrow-${type}`, viewBox: '0 0 10 10', refX: 9, refY: 5,
        markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse'});
      marker.append(svg('path', {d: 'M0 1 L10 5 L0 9 z', class: `arrow ${type}`}));
      defs.append(marker);
    }
    this.svg.append(defs, this.layer);
    try {
      this.engine = new window.ELK({workerUrl: '/static/vendor/elk/elk-worker.min.js'});
    } catch (error) {
      this.message(error.message);
    }
    this.context = document.createElement('canvas').getContext('2d');
    this.camera = {x: 0, y: 0, k: 1};
    this.positions = new Map();
    this.cards = new Map();
    this.routes = [];
    this.bind();
  }

  bind() {
    new ResizeObserver(() => {
      const {clientWidth: w, clientHeight: h} = this.frame;
      if (!w || !h) return;
      this.size = {w, h};
      this.svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
      if (this.fitPending) this.fit();
    }).observe(this.frame);

    this.svg.addEventListener('wheel', event => {
      event.preventDefault();
      const rect = this.svg.getBoundingClientRect();
      this.zoom(Math.exp(-event.deltaY * 0.0015), event.clientX - rect.left, event.clientY - rect.top);
    }, {passive: false});
    this.svg.addEventListener('pointerdown', event => {
      if (event.button !== 0 || event.target.closest('[data-node], .edge')) return;
      this.svg.focus({preventScroll: true});
      this.svg.setPointerCapture(event.pointerId);
      this.drag = {id: event.pointerId, x: event.clientX, y: event.clientY};
      this.svg.classList.add('panning');
    });
    this.svg.addEventListener('dblclick', event => {
      if (!event.target.closest('[data-node], .edge')) this.fit();
    });
    this.svg.addEventListener('pointermove', event => {
      if (this.drag?.id !== event.pointerId) return;
      this.pan(event.clientX - this.drag.x, event.clientY - this.drag.y);
      Object.assign(this.drag, {x: event.clientX, y: event.clientY});
    });
    this.svg.addEventListener('lostpointercapture', () => {
      this.drag = null;
      this.svg.classList.remove('panning');
    });
    this.svg.addEventListener('keydown', event => {
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      if (event.key === '+' || event.key === '=') { event.preventDefault(); this.zoom(1.2); return; }
      if (event.key === '-') { event.preventDefault(); this.zoom(1 / 1.2); return; }
      if (event.key === '0') { event.preventDefault(); this.fit(); return; }
      const step = event.shiftKey ? 96 : 32;
      const shifts = {ArrowLeft: [step, 0], ArrowRight: [-step, 0],
        ArrowUp: [0, step], ArrowDown: [0, -step]};
      if (!Object.hasOwn(shifts, event.key)) return;
      event.preventDefault();
      this.pan(...shifts[event.key]);
    });
  }

  message(text = '') {
    this.empty.textContent = text;
    this.empty.hidden = !text;
  }

  transform() {
    const {x, y, k} = this.camera;
    this.layer.setAttribute('transform', `translate(${x} ${y}) scale(${k})`);
  }

  pan(x, y) {
    this.camera.x += x;
    this.camera.y += y;
    this.transform();
  }

  zoom(factor, x = this.size?.w / 2, y = this.size?.h / 2) {
    if (!this.size) return;
    const {k} = this.camera;
    const next = Math.max(Math.min(0.025, k), Math.min(3, k * factor));
    this.camera.x = x - (x - this.camera.x) * next / k;
    this.camera.y = y - (y - this.camera.y) * next / k;
    this.camera.k = next;
    this.transform();
  }

  fit() {
    if (!this.size || !this.frame.clientWidth || !this.layout) {
      this.fitPending = true;
      return;
    }
    this.fitPending = false;
    const {w, h} = this.size;
    const {width, height} = this.layout;
    const k = Math.min(1.12, Math.max(1, w - 48) / Math.max(1, width),
      Math.max(1, h - 48) / Math.max(1, height));
    this.camera = {x: (w - width * k) / 2, y: (h - height * k) / 2, k};
    this.transform();
  }

  reset() {
    this.cache = null;
    this.request = null;
    this.layout = null;
    this.positions.clear();
    this.layer.replaceChildren();
    this.fitPending = true;
    this.revealKey = null;
  }

  reveal(key) {
    this.revealKey = key;
    if (!this.working) this.revealSelected();
  }

  revealSelected() {
    const bounds = this.positions.get(this.revealKey);
    if (!bounds || !this.size || !this.frame.clientWidth) return;
    this.revealKey = null;
    const cx = bounds.x + Math.min(bounds.width, 244) / 2;
    const cy = bounds.y + Math.min(bounds.height, 114) / 2;
    if (this.camera.k < 0.55) {
      const nextK = 0.85;
      this.camera = {x: this.size.w / 2 - cx * nextK, y: this.size.h / 2 - cy * nextK, k: nextK};
      this.transform();
      return;
    }
    const {x, y, k} = this.camera;
    const px = x + cx * k;
    const py = y + cy * k;
    if (px < 24 || py < 24 || px > this.size.w - 24 || py > this.size.h - 24) {
      this.pan(this.size.w / 2 - px, this.size.h / 2 - py);
    }
  }

  update(projection, selected, connect) {
    this.request = {key: geometryKey(projection), projection, selected, connect};
    if (!this.working) void this.renderLatest();
  }

  async renderLatest() {
    this.working = true;
    try {
      while (this.request) {
        const {key, projection} = this.request;
        if (this.cache?.key !== key) {
          this.svg.setAttribute('aria-busy', 'true');
          this.message('正在布局…');
          const layout = await computeLayout(projection, {engine: this.engine});
          if (this.request?.key !== key) continue;
          this.cache = {key, layout};
        }
        const request = this.request;
        this.request = null;
        this.paint(this.cache.layout, request);
        this.message(this.layout.children.length ? '' : '没有符合条件的图谱节点');
      }
    } catch (error) {
      this.request = null;
      this.svg.setAttribute('aria-busy', 'false');
      this.message(error.message);
    } finally {
      this.working = false;
    }
  }

  paint(layout, {projection, selected, connect}) {
    const old = this.positions.get(selected);
    const metadata = new Map(projection.edges.map(edge => [edge.id, edge.data]));
    function index(node) {
      metadata.set(node.id, node.data);
      node.children?.forEach(index);
    }
    projection.children.forEach(index);
    const focus = document.activeElement;
    const focused = focus?.closest('[data-node]')?.getAttribute('data-node');
    const containers = svg('g');
    const edges = svg('g');
    const cards = svg('g');
    this.cards.clear();
    this.routes = [];
    this.positions = new Map();
    this.layout = layout;
    this.svg.classList.toggle('connecting', !!connect);
    this.svg.setAttribute('aria-busy', 'false');

    const visit = (node, x, y) => {
      const bounds = {x: x + node.x, y: y + node.y, width: node.width, height: node.height};
      this.positions.set(node.id, bounds);
      const data = metadata.get(node.id);
      if (node.children?.length) {
        containers.append(svg('rect', {x: bounds.x, y: bounds.y, width: node.width, height: node.height,
          class: 'compound-frame'}));
      }
      const card = this.card(node, bounds, data, selected, connect);
      this.cards.set(node.id, card);
      cards.append(card);
      node.children?.forEach(child => visit(child, bounds.x, bounds.y));
    };
    layout.children.forEach(node => visit(node, 0, 0));

    for (const edge of layout.edges) {
      const {type, change, edges: items} = metadata.get(edge.id);
      const origin = this.positions.get(edge.container) || {x: 0, y: 0};
      const group = svg('g', {class: `edge ${type} ${change}`, transform: `translate(${origin.x} ${origin.y})`});
      const label = relationLabels[type] || type;
      const desc = (items || []).map(row => type === 'requires'
        ? `#${row.blocker_id} → #${row.blocked_id}`
        : `${row.source_note_id} → ${row.target_type === 'ticket' ? `#${row.target_id}` : row.target_id}`);
      if (desc.length) group.append(svg('title', {}, `${label}\n${desc.join('\n')}`));
      const outgoing = type === 'relates' ? {} : {'marker-end': `url(#arrow-${type})`};
      for (const section of edge.sections || []) {
        const points = [section.startPoint, ...(section.bendPoints || []), section.endPoint];
        const d = points.map(({x, y}, i) => `${i ? 'L' : 'M'}${x},${y}`).join(' ');
        group.append(
          svg('path', {d, class: 'edge-hit'}),
          svg('path', {d, ...(!section.outgoingSections?.length ? outgoing : {})}),
        );
      }
      group.addEventListener('click', event => {
        event.stopPropagation();
        const first = items?.[0];
        if (!first) return;
        if (type === 'requires') this.actions.pickNode('ticket', first.blocked_id);
        else this.actions.pickNode('note', first.source_note_id);
      });
      for (const label of edge.labels || []) {
        group.append(svg('text', {x: label.x + label.width / 2, y: label.y + 12,
          'text-anchor': 'middle'}, label.text));
      }
      this.routes.push({group, source: edge.sources[0], target: edge.targets[0]});
      edges.append(group);
    }
    this.layer.replaceChildren(containers, edges, cards);
    const next = this.positions.get(selected);
    if (this.fitPending) this.fit();
    else if (old && next) this.pan((old.x - next.x) * this.camera.k, (old.y - next.y) * this.camera.k);
    this.revealSelected();
    if (focused) {
      const card = this.cards.get(focused);
      const control = focus.classList.contains('node-toggle') ? card?.querySelector('.node-toggle') : card;
      control?.focus({preventScroll: true});
    }
  }

  card(node, {x, y}, data, selected, connect) {
    const {ticket, note, hasChildren, collapsed, drift} = data;
    const item = ticket || note;
    const width = item ? Math.min(244, node.width) : node.width;
    const height = item ? 114 : collapsed ? 60 : 42;
    const caption = item?.title ?? data.label;
    const classes = ['graph-node', ticket ? 'ticket' : note ? 'note' : 'group', item?._proposal,
      selected === node.id ? 'selected' : '', connect?.key === node.id ? 'connect-source' : ''].filter(Boolean);
    const card = svg('g', {class: classes.join(' '), transform: `translate(${x} ${y})`,
      'data-node': node.id, tabindex: 0, role: 'button', 'aria-label': caption});
    const toggle = () => this.actions.collapse(node.id);
    const activate = () => item ? this.actions.pickNode(ticket ? 'ticket' : 'note', item.id) : toggle();
    card.append(svg('title', {}, caption), svg('rect', {class: 'node-surface', width, height}));
    if (item) {
      if (typeof item.id !== 'number' && ticket && item.id.startsWith('new:')) card.classList.add('preview-item');
      const label = ticket ? `#${item.id}  /  ${item.status}` : `${item.kind}  /  ${item.state}`;
      card.append(svg('text', {x: 12, y: 19, class: 'node-meta'}, label));
    }
    this.context.font = '600 14px system-ui, sans-serif';
    const lines = wrapText(caption, width - (hasChildren ? 58 : 27),
      text => this.context.measureText(text).width, item ? 2 : 1);
    lines.forEach((line, i) => card.append(svg('text', {
      class: 'node-title', x: 12, y: (item ? 41 : 25) + 17 * i,
    }, line)));
    const count = ticket?.blockers?.length || 0;
    const premise = ticket?.review_notes?.length || note?.adoption_stale;
    this.badges(card, 67, [
      [count ? 'blocked' : 'ready', count ? `${count} 项阻塞` : ticket?.ready ? '可执行' : ''],
      ['premise', premise ? '前提待复核' : ''],
    ]);
    this.badges(card, item ? 88 : 37, [
      ['drifted', drift?.drifted ? `代码已漂移 ${drift.drifted}` : ''],
      ['missing', drift?.missing ? `锚点缺失 ${drift.missing}` : ''],
    ]);
    if (hasChildren) {
      const control = svg('g', {class: 'node-toggle', transform: `translate(${width - 29} 8)`,
        tabindex: 0, role: 'button', 'aria-label': collapsed ? '展开' : '折叠', 'aria-expanded': !collapsed});
      control.append(svg('rect', {width: 21, height: 21}), svg('text', {x: 10.5, y: 15, 'text-anchor': 'middle'},
        collapsed ? '+' : '−'));
      control.addEventListener('click', event => { event.stopPropagation(); toggle(); });
      control.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          event.stopPropagation();
          toggle();
        }
      });
      card.append(control);
    }
    card.addEventListener('click', activate);
    card.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        activate();
      }
    });
    card.addEventListener('pointerenter', () => this.highlight(node.id));
    card.addEventListener('pointerleave', () => this.highlight(null));
    return card;
  }

  badges(card, y, values) {
    this.context.font = '11px system-ui, sans-serif';
    let x = 12;
    for (const [style, label] of values) {
      if (!label) continue;
      const width = Math.ceil(this.context.measureText(label).width) + 10;
      const badge = svg('g', {class: `node-badge ${style}`, transform: `translate(${x} ${y})`});
      badge.append(svg('rect', {width, height: 16}), svg('text', {x: 5, y: 12}, label));
      card.append(badge);
      x += width + 5;
    }
  }

  highlight(id) {
    const neighbors = new Set([id]);
    for (const {group, source, target} of this.routes) {
      const adjacent = source === id || target === id;
      group.classList.toggle('highlighted', adjacent);
      group.classList.toggle('muted-edge', !!id && !adjacent);
      if (adjacent) {
        neighbors.add(source);
        neighbors.add(target);
      }
    }
    for (const [key, card] of this.cards) {
      card.classList.toggle('neighbor', !!id && neighbors.has(key));
    }
  }
}
