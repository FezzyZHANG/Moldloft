export const byId = id => document.getElementById(id);

export function element(tag, {className, text, ...attributes} = {}, ...children) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  for (const [key, value] of Object.entries(attributes)) {
    if (value != null) node.setAttribute(key, value);
  }
  node.append(...children);
  return node;
}

export function button(text, action, attributes) {
  const node = element('button', {type: 'button', text, ...attributes});
  node.addEventListener('click', action);
  return node;
}

export function options(select, rows, value) {
  select.replaceChildren(...rows.map(([key, label]) => element('option', {value: key, text: label})));
  if (value !== undefined) select.value = value;
  return select;
}

export function field(label, control) {
  return element('label', {className: 'field'}, element('span', {text: label}), control);
}

export function diffBlock(diff) {
  return element('pre', {className: 'unified-diff'}, ...diff.trimEnd().split('\n').map(line =>
    element('span', {text: line || ' ', className: /^(---|\+\+\+|@@)/.test(line) ? 'diff-meta' :
      line.startsWith('+') ? 'added' : line.startsWith('-') ? 'removed' : ''})));
}

export function showDialog(dialog) {
  dialog.querySelector('[role="alert"]').textContent = '';
  if (!dialog.open) dialog.showModal();
}

export function bindForm(dialog, submit) {
  const form = dialog.querySelector('form');
  const status = dialog.querySelector('[role="alert"]');
  const confirm = form.querySelector('button[type="submit"]');
  let busy = false;
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy) return;
    status.textContent = '';
    busy = true;
    confirm.disabled = true;
    try {
      const after = await submit(form);
      dialog.close();
      await after?.();
    } catch (error) {
      if (!dialog.open) dialog.showModal();
      status.textContent = error.message;
    } finally {
      confirm.disabled = false;
      busy = false;
    }
  });
  dialog.querySelector('[data-close]').addEventListener('click', () => { if (!busy) dialog.close(); });
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  return form.elements;
}

export const statuses = [
  ['open', '待办'], ['in_progress', '进行中'], ['done', '完成'], ['closed', '关闭'],
];
export const priorities = [['low', '低'], ['medium', '中'], ['high', '高']];
export const noteStates = {
  assumption: ['proposed', 'adopted', 'refuted', 'retired'],
  question: ['open', 'resolved'],
  evidence: ['recorded', 'superseded'],
  decision: ['proposed', 'accepted', 'superseded'],
  note: ['active', 'archived'],
};
export const relationLabels = {requires: '依赖', assumes: '前提', supports: '支持', challenges: '反证', relates: '关联'};
