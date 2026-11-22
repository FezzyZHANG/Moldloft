import {api, findItem, graphPath} from './store.js';
import {bindForm, button, byId, diffBlock, element, options, showDialog} from './ui.js';

export class AnchorPanel {
  constructor(store, actions, section) {
    this.store = store;
    this.actions = actions;
    this.section = section;
    this.dialog = byId('anchor-dialog');
    this.controls = bindForm(this.dialog, async () => {
      const {target, graphId} = this.context;
      const graph = store.graph;
      if (graph.id !== graphId) throw new Error('图谱已切换');
      const read = name => this.controls.namedItem(name);
      const anchor = {repo: read('repo').value, kind: read('kind').value, ref: read('ref').value.trim(),
        symbol: read('symbol').value, claim: read('claim').value};
      if (anchor.kind === 'file') {
        anchor.path = read('path').value.trim();
        for (const name of ['start_line', 'end_line']) {
          if (read(name).value) anchor[name] = read(name).valueAsNumber;
        }
      }
      await store.mutate([{op: 'anchor_add', target_type: target.type, target_id: target.id, anchor}], graph);
      return () => actions.run(async () => {
        await store.refresh(graphId);
        await store.verifyTarget(target);
      });
    });
    this.controls.namedItem('repo').addEventListener('change', () => void this.refreshRefs());
    this.controls.namedItem('kind').addEventListener('change', () => void this.refreshRefs());
  }

  add(target, suggestion) {
    const {id: graphId, repos} = this.store.graph;
    const selected = this.store.selection;
    if (selected?.id !== target.id || selected.type !== target.type) return;
    if (!repos.length) throw new Error('没有已配置的 Git 仓库');
    this.context = {target, graphId};
    this.dialog.querySelector('form').reset();
    options(this.controls.namedItem('repo'), repos.map(repo => [repo, repo]), suggestion?.repo);
    if (suggestion) {
      for (const name of ['path', 'start_line', 'end_line']) {
        this.controls.namedItem(name).value = suggestion[name] ?? '';
      }
    }
    showDialog(this.dialog);
    void this.refreshRefs();
  }

  async refreshRefs() {
    const repo = this.controls.namedItem('repo').value;
    const kind = this.controls.namedItem('kind').value;
    const ref = this.controls.namedItem('ref');
    const message = byId('git-ref-status');
    const picker = byId('git-ref-options');
    const wanted = ref.value;
    byId('anchor-file-fields').hidden = kind !== 'file';
    this.controls.namedItem('path').required = kind === 'file';
    const request = (this.request || 0) + 1;
    this.request = request;
    picker.replaceChildren();
    message.textContent = '正在读取 Git refs…';
    try {
      const {head, branches, commits} = await api(`/api/git/${encodeURIComponent(repo)}/refs`);
      if (request !== this.request || !this.dialog.open) return;
      const rows = [
        ...(kind !== 'branch' ? [['HEAD', head.slice(0, 12)]] : []),
        ...branches.map(branch => [branch, 'branch']),
        ...(kind !== 'branch' ? commits.map(({sha, date, subject}) => [sha, `${date} ${subject}`]) : []),
      ];
      picker.replaceChildren(...rows.map(([value, label]) => element('option', {value, label})));
      if (kind === 'branch' && ref.value === wanted && !branches.includes(wanted)) ref.value = branches[0] || '';
      if (kind !== 'branch' && !ref.value) ref.value = 'HEAD';
      message.textContent = `HEAD ${head.slice(0, 12)} · ${branches.length} 个分支 · ${commits.length} 条近期提交`;
    } catch (error) {
      if (request === this.request) message.textContent = error.message;
    }
  }

  async loadSuggestions(target, chips) {
    const query = new URLSearchParams({target: target.id});
    try {
      const {suggestions} = await api(`${graphPath(this.store.graph.id)}/anchor-suggestions?${query}`);
      for (const suggestion of suggestions) {
        const open = () => this.actions.run(() => this.actions.guard(() => this.add(target, suggestion)));
        chips.append(button(`+ ${suggestion.repo}:${suggestion.path}`, open));
      }
    } catch (error) {
      chips.textContent = error.message;
    }
  }

  render(target) {
    const {store, actions, section} = this;
    const task = operation => () => actions.run(() => actions.guard(operation));
    const verify = () => actions.run(() => store.verifyTarget(target));
    const open = new Set([...section.querySelectorAll('details[open]')].map(node => node.dataset.anchor));
    const matching = store.graph.anchors.filter(a => a.target_type === target.type && a.target_id === target.id);
    const title = element('div', {className: 'section-heading'},
      element('h3', {text: 'Ground Truth 锚点'}),
      button('校验', verify, {disabled: matching.length ? null : ''}),
      button('添加锚点', task(() => this.add(target)), {disabled: store.graph.repos.length ? null : ''}));
    const rows = matching.map(anchor => {
      const result = store.drift.get(anchor.id);
      const location = anchor.path ? `${anchor.path}:${anchor.start_line}–${anchor.end_line}` : anchor.ref;
      const details = element('details', {'data-anchor': anchor.id, open: open.has(String(anchor.id)) ? '' : null},
        element('summary', {text: result?.diff ? '固定摘录 / 统一差异' : '固定摘录'}),
        element('pre', {text: anchor.excerpt || '无摘录'}));
      if (result?.diff) details.append(diffBlock(result.diff));
      const reanchor = task(async () => {
        await api(`/api/anchors/${anchor.id}/reanchor`, 'POST',
          result?.status === 'moved' && result.suggested_pin ? result.suggested_pin : {});
        await store.refresh();
        await store.verifyTarget(target);
      });
      const remove = task(async () => {
        if (!confirm(`移除锚点 ${location}？`)) return;
        await store.mutate([{op: 'anchor_remove', anchor_id: anchor.id}]);
        await store.refresh();
      });
      return element('article', {className: 'anchor-card'},
        element('div', {className: 'anchor-caption'}, element('strong', {text: anchor.repo}),
          element('span', {className: `badge ${result?.status || ''}`, text: result?.status || '未校验'})),
        element('code', {text: `${anchor.kind} · ${anchor.ref}`}),
        ...(anchor.ref !== anchor.pinned_sha ? [element('code', {
          text: `固定提交 ${anchor.pinned_sha.slice(0, 12)}`, title: anchor.pinned_sha,
        })] : []),
        ...(anchor.path ? [element('code', {text: location})] : []),
        ...(anchor.symbol ? [element('code', {text: anchor.symbol})] : []),
        ...(anchor.claim ? [element('p', {text: anchor.claim})] : []),
        ...(result ? [element('p', {className: 'muted', text: [result.message,
          result.current_range?.join('–'), result.working_dirty && result.status !== 'dirty' ? '工作区有改动' : '']
          .filter(Boolean).join(' · ')})] : []),
        details,
        element('div', {className: 'toolbar'},
          button(anchor.kind === 'branch' ? '重新固定分支' : '重新锚定到 HEAD', reanchor,
            {disabled: result?.status === 'missing' ? '' : null}),
          button('移除', remove, {className: 'danger'})));
    });
    section.replaceChildren(title, ...rows);
    if (!rows.length) section.append(element('p', {className: 'empty',
      text: store.graph.repos.length ? '暂无代码锚点' : '暂无代码锚点 · 当前未配置 Git 仓库'}));
    if (store.graph.repos.length) {
      const item = findItem(store.graph, target);
      if (this.suggestedFor !== item) {
        this.suggestedFor = item;
        this.chips = element('div', {className: 'anchor-suggestions'});
        void this.loadSuggestions(target, this.chips);
      }
      section.append(this.chips);
    }
  }
}
