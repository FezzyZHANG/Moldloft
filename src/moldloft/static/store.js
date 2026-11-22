import {edgeTypes, itemKey} from './layout.js';

export class ApiError extends Error {
  constructor(status, data) {
    const detail = Array.isArray(data.detail) ? data.detail.map(error =>
      `${error.loc.join('.')}: ${error.msg}`).join(' · ') : data.detail;
    super(data.message || detail || `HTTP ${status}`);
    this.status = status;
    this.data = data;
  }
}

export async function api(path, method = 'GET', body) {
  const options = {method, headers: {Accept: 'application/json'}};
  if (body !== undefined) {
    if (!body || typeof body !== 'object' || Array.isArray(body)) throw new TypeError('Expected a JSON object');
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, data || {});
  if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('API response is not a JSON object');
  return data;
}

export const graphPath = id => `/api/graphs/${encodeURIComponent(id)}`;
export const proposalPath = id => `/api/proposals/${encodeURIComponent(id)}`;
export const targetName = (type, id) => type === 'ticket' ? `#${id}` : id;

export function readTarget(value) {
  if (/^(?:t:|ticket:|#)?[1-9]\d*$/.test(value || '')) {
    const id = Number(value.replace(/^(t:|ticket:|#)/, ''));
    return Number.isSafeInteger(id) ? {type: 'ticket', id} : null;
  }
  const note = value?.replace(/^(n:|note:)/, '');
  if (/^n-[a-z0-9][a-z0-9-]{0,62}$/.test(note || '')) return {type: 'note', id: note};
  if (value?.startsWith('proposal:')) return {type: 'proposal', id: value.slice(9)};
  return null;
}

export function targetParam(target) {
  if (!target) return '';
  if (target.type === 'proposal') return `proposal:${target.id}`;
  return String(target.id);
}

export function findItem(graph, target) {
  if (!target || target.type === 'proposal') return null;
  return graph?.[`${target.type}s`].find(row => row.id === target.id) || null;
}

const anchorStamp = anchor => JSON.stringify([
  anchor.repo, anchor.kind, anchor.ref, anchor.pinned_sha, anchor.path, anchor.start_line, anchor.end_line,
  anchor.content_hash, anchor.target_type, anchor.target_id,
]);

export class WorkbenchStore {
  graphs = [];
  graph = null;
  selection = null;
  proposals = [];
  inspection = null;
  drift = new Map();
  connection = 'offline';
  collapsed = new Set();
  view = {mode: 'graph', search: '', status: '', group: '', local: false, direction: 'both', depth: 1,
    showNotes: true, types: new Set(edgeTypes), connect: null};
  listeners = new Set();
  opening = 0;
  graphEpoch = 0;
  loading = 0;
  appliedLoad = 0;
  verifying = 0;
  verificationOrder = new Map();

  constructor(report) {
    this.report = report;
  }

  subscribe(listener) {
    this.listeners.add(listener);
  }

  emit(reason) {
    for (const listener of this.listeners) listener(reason);
  }

  async listGraphs() {
    this.graphs = (await api('/api/graphs')).graphs;
    this.emit('graphs');
    return this.graphs;
  }

  async open(id, selection, beforeCommit) {
    const request = ++this.opening;
    const [graph, proposals] = await this.read(id);
    if (request !== this.opening || !await beforeCommit()) return false;
    if (request !== this.opening) return false;
    this.stopEvents();
    this.graphEpoch++;
    this.graph = graph;
    this.proposals = proposals;
    this.selection = selection?.type === 'proposal' || findItem(graph, selection) ? selection : null;
    this.inspection = null;
    this.drift.clear();
    this.verificationOrder.clear();
    this.collapsed.clear();
    this.view.connect = null;
    this.view.group = '';
    this.pendingSig = null;
    this.connection = 'connecting';
    this.emit('open');
    this.startEvents(id);
    return true;
  }

  async read(id) {
    const path = graphPath(id);
    const [graph, pending] = await Promise.all([api(path), api(`${path}/proposals?status=pending&limit=50`)]);
    return [graph, pending.proposals];
  }

  async refresh(id = this.graph?.id) {
    if (!id || this.graph?.id !== id) return;
    const epoch = this.graphEpoch;
    const request = ++this.loading;
    const [graph, proposals] = await this.read(id);
    if (epoch !== this.graphEpoch || request < this.appliedLoad || graph.revision < this.graph.revision) return;
    this.appliedLoad = request;
    const old = new Map(this.graph.anchors.map(anchor => [anchor.id, anchorStamp(anchor)]));
    const current = new Map(graph.anchors.map(anchor => [anchor.id, anchorStamp(anchor)]));
    for (const key of this.drift.keys()) if (old.get(key) !== current.get(key)) this.drift.delete(key);
    this.graph = graph;
    this.proposals = proposals;
    this.emit('snapshot');
  }

  stopEvents() {
    this.events?.close();
    this.events = null;
    clearInterval(this.poll);
    this.poll = null;
  }

  startEvents(id) {
    const reload = () => this.refresh(id).catch(this.report);
    const source = new EventSource(`${graphPath(id)}/events`);
    this.events = source;
    source.onopen = () => {
      if (this.events !== source) return;
      clearInterval(this.poll);
      this.poll = null;
      this.connection = 'online';
      this.emit('connection');
      reload();
    };
    source.addEventListener('revision', event => {
      if (this.events !== source) return;
      const {revision, pending_sig} = JSON.parse(event.data);
      if (revision !== this.graph.revision || pending_sig !== this.pendingSig) {
        this.pendingSig = pending_sig;
        reload();
      }
    });
    source.onerror = () => {
      if (this.events !== source) return;
      this.connection = 'offline';
      this.emit('connection');
      if (!this.poll) this.poll = setInterval(reload, 5000);
    };
  }

  select(target) {
    this.selection = target;
    this.inspection = null;
    this.emit('selection');
  }

  updateView(fields) {
    Object.assign(this.view, fields);
    this.emit('view');
  }

  search(query, {kind, limit = 30, graph = this.graph?.id} = {}) {
    if (!graph || !query.trim()) return Promise.resolve({results: []});
    const params = new URLSearchParams({q: query, limit});
    if (kind) params.set('kind', kind);
    return api(`${graphPath(graph)}/search?${params}`);
  }

  mutate(operations, graph = this.graph) {
    return api(`${graphPath(graph.id)}/mutate`, 'POST', {operations, revision: graph.revision});
  }

  verifyGraph() { return this.verify(); }
  verifyTarget(target) { return this.verify(target); }

  async verify(target) {
    const graph = this.graph;
    if (!graph) return [];
    const epoch = this.graphEpoch;
    const order = ++this.verifying;
    const pinned = new Map(graph.anchors.map(anchor => [anchor.id, anchorStamp(anchor)]));
    const query = target ? `?${new URLSearchParams({target: `${target.type}:${target.id}`})}` : '';
    const {results} = await api(`${graphPath(graph.id)}/verify${query}`);
    if (epoch !== this.graphEpoch) return [];
    const active = new Map(this.graph.anchors.map(anchor => [anchor.id, anchorStamp(anchor)]));
    for (const result of results) {
      const key = result.anchor_id;
      if (!active.has(key) || pinned.get(key) !== active.get(key) ||
          order < (this.verificationOrder.get(key) || 0)) continue;
      this.drift.set(key, result);
      this.verificationOrder.set(key, order);
    }
    this.emit('drift');
    return results;
  }

  get selectedKey() {
    const target = this.selection;
    return target && target.type !== 'proposal' ? itemKey(target.type, target.id) : null;
  }
}
