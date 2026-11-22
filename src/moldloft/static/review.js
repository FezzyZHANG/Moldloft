import {proposalPath, api} from './store.js';
import {button, byId, diffBlock, element, field} from './ui.js';

const amendable = new Set(['title', 'description', 'tech_notes', 'group_name', 'summary', 'body', 'claim', 'symbol']);
const entityNames = {tickets: '工单', notes: '笔记', edges: '依赖', note_links: '笔记链接', anchors: '锚点'};
const changeNames = {create: '新增', delete: '删除', update: '修改'};
const stateNames = {pending: '待审核', expired: '已过期', applied: '已应用', rejected: '已拒绝'};
const showValue = value => value === null ? '∅' : typeof value === 'string' ? value : JSON.stringify(value);

export class Review {
  constructor(store, actions) {
    this.store = store;
    this.actions = actions;
    this.root = byId('review');
    this.request = 0;
  }

  select(target) {
    const graphId = target ? this.store.graph?.id : null;
    if (target?.id === this.id && graphId === this.graphId) return;
    this.id = target?.id;
    this.graphId = graphId;
    this.proposal = null;
    this.message = null;
    this.live = false;
    this.busy = false;
    clearInterval(this.timer);
    this.request++;
    this.root.hidden = !this.id;
    this.root.replaceChildren();
    if (!this.id) return;
    this.root.append(element('p', {className: 'inspector-empty', text: '正在读取提案…'}));
    void this.refresh();
    this.timer = setInterval(() => this.refresh(), 5000);
  }

  async refresh() {
    if (!this.id || this.busy) return;
    const request = ++this.request;
    try {
      const proposal = await api(proposalPath(this.id));
      if (request !== this.request) return;
      if (proposal.graph_id !== this.store.graph?.id) throw new Error('该提案属于另一张图谱');
      const previous = this.proposal;
      const stale = !this.live;
      this.live = true;
      this.proposal = proposal;
      if (!previous) this.build();
      if (stale || JSON.stringify(previous) !== JSON.stringify(proposal)) {
        this.render();
        this.store.inspection = proposal;
        this.store.emit('inspection');
      }
      this.updateButtons();
    } catch (error) {
      if (request !== this.request) return;
      this.live = false;
      if (this.message) {
        this.reason.textContent = error.message;
        this.updateButtons();
      } else this.root.replaceChildren(element('p', {className: 'form-error', text: error.message}));
    }
  }

  build() {
    const {summary, rationale, operations} = this.proposal;
    this.amendments = operations.map((op, index) => {
      const text = Object.entries(op.fields || op.anchor || {})
        .filter(([key, value]) => amendable.has(key) && typeof value === 'string');
      if (!text.length) return null;
      const fields = text.map(([key, original]) => {
        const input = element('textarea', {rows: 2});
        input.value = original;
        return {key, original: input.value, input};
      });
      return {index, fields, section: element('details', {}, element('summary', {text: `${index + 1}. ${op.op}`}),
        ...fields.map(({key, input}) => field(key, input)))};
    }).filter(Boolean);
    this.status = element('span', {className: 'badge'});
    this.reason = element('p', {className: 'muted'});
    this.changes = element('div');
    this.note = element('textarea', {rows: 3});
    this.message = element('p', {className: 'form-error', role: 'alert'});
    this.apply = button('应用批次', () => void this.decide('apply'), {className: 'accent'});
    this.reject = button('拒绝', () => void this.decide('reject'), {className: 'danger'});
    this.note.addEventListener('input', () => this.updateButtons());
    this.decision = element('section', {className: 'inspector-section'},
      ...this.amendments.length ? [element('h3', {text: '修订提案文字'}),
        ...this.amendments.map(({section}) => section)] : [],
      field('决定说明（拒绝时必填）', this.note),
      this.message, element('div', {className: 'toolbar'}, this.apply, this.reject));
    this.record = element('section', {className: 'inspector-section', hidden: ''});
    this.root.replaceChildren(
      element('div', {className: 'inspector-heading'},
        element('h2', {text: '审核提案'}), this.status),
      element('h3', {text: summary}), element('p', {className: 'proposal-rationale', text: rationale}),
      this.reason, button('重新检查适用性', () => void this.refresh()),
      element('section', {className: 'inspector-section'}, element('h3', {text: '提案变更'}), this.changes),
      this.decision, this.record);
  }

  render() {
    const proposal = this.proposal;
    this.status.textContent = proposal.status === 'pending' && !proposal.applicable ?
      '当前不可应用' : stateNames[proposal.status];
    this.status.className = `badge ${proposal.applicable ? 'fresh' : ''}`;
    this.reason.textContent = proposal.reason || (proposal.applicable ? '当前版本可应用' : '');
    const open = new Set([...this.changes.querySelectorAll('details[open]')].map(node => node.dataset.key));
    this.changes.replaceChildren(...proposal.changes.map(change => {
      const {entity, id, action, fields} = change;
      const key = JSON.stringify([entity, id]);
      const style = {create: 'added', delete: 'deleted', update: 'changed'}[action];
      return element('details', {className: `proposal-change ${style}`, 'data-key': key,
        open: open.has(key) ? '' : null},
      element('summary', {text: `${changeNames[action]} ${entityNames[entity]} ${Array.isArray(id) ? id.join(' / ') : id}`}),
      ...Object.entries(fields).map(([name, delta]) => element('div', {className: 'field-diff'},
        element('strong', {text: name}),
        delta.diff ? diffBlock(delta.diff) : element('div', {},
          element('pre', {className: 'removed', text: showValue(delta.before)}),
          element('pre', {className: 'added', text: showValue(delta.after)})))));
    }));
    if (!proposal.changes.length) this.changes.append(element('p', {className: 'empty', text: '暂无可显示的变更'}));
    this.decision.hidden = proposal.status !== 'pending';
    this.record.hidden = proposal.status === 'pending';
    this.record.replaceChildren(element('h3', {text: '审核决定'}),
      element('p', {text: proposal.decision_note || stateNames[proposal.status]}));
    this.updateButtons();
  }

  updateButtons() {
    this.apply.disabled = this.busy || !this.live || !this.proposal.applicable;
    this.reject.disabled = this.busy || !this.note.value.trim();
  }

  async decide(method) {
    if (this.busy || this.proposal.status !== 'pending' || (method === 'apply' && !this.live)) return;
    const id = this.id;
    const graph = this.store.graph.id;
    const note = this.note.value.trim();
    const edits = this.amendments.flatMap(({index, fields}) => {
      const changed = Object.fromEntries(fields.filter(entry => entry.original !== entry.input.value)
        .map(({key, input}) => [key, input.value]));
      return Object.keys(changed).length ? [{op_index: index, fields: changed}] : [];
    });
    this.busy = true;
    this.request++;
    this.message.textContent = '';
    this.updateButtons();
    try {
      const payload = method === 'apply' ? {edits, note} : {reason: note};
      await api(`${proposalPath(id)}/${method}`, 'POST', payload);
    } catch (error) {
      if (this.id === id && this.graphId === graph) this.message.textContent = error.message;
      this.actions.report(error);
    } finally {
      if (this.id === id && this.graphId === graph) {
        this.busy = false;
        this.updateButtons();
      }
      await this.store.refresh(graph).catch(this.actions.report);
    }
  }
}
