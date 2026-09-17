// A page owns one immutable scope. Catalog changes never retarget an open editor.
export function scopedUrl(path, drydockId) {
  const url = new URL(path, location.origin);
  if (drydockId === 'default') url.searchParams.delete('drydock');
  else url.searchParams.set('drydock', drydockId);
  return url.pathname + url.search + url.hash;
}

export function setupDrydocks({act = action => action(), beforeNavigate = () => {}, report} = {}) {
  const $ = id => document.getElementById(id);
  const currentId = document.body.dataset.drydockId;
  const select = $('drydock-select'), dialog = $('drydock-dialog'), form = $('drydock-form');
  let current = {id: currentId, name: document.body.dataset.drydockName, version: Number(document.body.dataset.drydockVersion)};
  const suffix = ` · ${current.name} · Moldloft`;
  const titlePrefix = document.title.endsWith(suffix) ? document.title.slice(0, -suffix.length) : 'Moldloft';
  let busy = false, mode, intent = null;
  const showError = error => {
    const target = dialog.open ? $('drydock-form-error') : $('drydock-error');
    target.textContent = error.message || String(error); target.hidden = false;
  };
  report ||= showError;
  async function request(path, options = {}) {
    const response = await fetch(path, {cache: 'no-store', ...options, headers: {'Content-Type': 'application/json'}});
    let data;
    try { data = await response.json(); } catch { throw new Error('Drydock 响应未完整返回，请重试'); }
    if (!response.ok) {
      const error = new Error(data.message || 'Drydock 请求未完成'); error.data = data; throw error;
    }
    return data;
  }
  function displayCurrent(row) {
    current = row;
    const option = [...select.options].find(item => item.value === currentId);
    if (option) option.textContent = row.name;
    select.title = row.name;
    document.body.dataset.drydockName = row.name;
    document.body.dataset.drydockVersion = row.version;
    document.title = `${titlePrefix} · ${row.name} · Moldloft`;
  }
  async function refreshCatalog() {
    const catalog = await request('/api/drydocks');
    if (busy || dialog.open) return;
    select.replaceChildren(...catalog.items.map(row => {
      const option = document.createElement('option'); option.value = row.id; option.textContent = row.name; return option;
    }));
    select.value = currentId;
    const row = catalog.items.find(item => item.id === currentId);
    if (row) displayCurrent(row);
  }
  function setBusy(value) {
    busy = value;
    for (const id of ['drydock-select', 'drydock-create', 'drydock-rename', 'drydock-submit', 'drydock-cancel', 'drydock-name']) $(id).disabled = value;
  }
  async function navigate(id, path = '/graph') {
    // An unavailable destination must leave the acknowledged current page usable.
    if (id !== currentId) await request(scopedUrl('/api/graph', id));
    beforeNavigate();
    location.assign(scopedUrl(path, id));
  }
  async function choose(id) {
    select.value = currentId;
    if (busy || id === currentId) return;
    setBusy(true);
    try { await act(() => navigate(id)); }
    catch (error) { report(error); }
    finally { setBusy(false); }
  }
  select.addEventListener('change', () => choose(select.value));
  select.addEventListener('focus', () => refreshCatalog().catch(report));
  window.addEventListener('focus', () => refreshCatalog().catch(report));

  async function open(kind) {
    if (busy) return;
    try {
      await act(() => {
        mode = kind; intent = null;
        $('drydock-dialog-title').textContent = kind === 'create' ? '新建 Drydock' : '重命名 Drydock';
        $('drydock-name').value = kind === 'create' ? '' : current.name;
        $('drydock-submit').textContent = kind === 'create' ? '创建并打开' : '保存名称';
        $('drydock-form-error').hidden = true;
        dialog.showModal(); $('drydock-name').focus(); $('drydock-name').select();
      });
    } catch (error) { report(error); }
  }
  $('drydock-create').onclick = () => open('create');
  $('drydock-rename').onclick = () => open('rename');
  $('drydock-cancel').onclick = () => dialog.close();
  dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (busy) return;
    const name = $('drydock-name').value.trim();
    if (!name) { showError(new Error('请填写 Drydock 名称')); return; }
    if (intent?.name !== name) intent = {name, request_id: crypto.randomUUID()};
    setBusy(true); $('drydock-form-error').hidden = true;
    try {
      await act(async () => {
        const row = await request(mode === 'create' ? '/api/drydocks' : `/api/drydocks/${currentId}`, {
          method: mode === 'create' ? 'POST' : 'PATCH',
          body: JSON.stringify(mode === 'create' ? intent : {name, version: current.version})});
        if (mode === 'create') await navigate(row.id);
        else { displayCurrent(row); dialog.close(); }
      });
    } catch (error) {
      if (error.data?.current) {
        displayCurrent(error.data.current);
        error.message = `名称已变为「${current.name}」，请检查后再保存。你的输入已保留。`;
      }
      showError(error);
    } finally { setBusy(false); }
  });
  for (const link of document.querySelectorAll('a[data-drydock-navigation]')) {
    link.addEventListener('click', async event => {
      if (event.button || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault(); if (busy) return;
      setBusy(true);
      try { await act(() => navigate(currentId, link.getAttribute('href'))); }
      catch (error) { report(error); }
      finally { setBusy(false); }
    });
  }
}

// Older list/detail forms require an explicit submit. Hold a switch while they are dirty.
if (!document.body.classList.contains('dag-app')) {
  const forms = [...document.querySelectorAll('form[method=post]')];
  const signature = form => JSON.stringify([...new FormData(form)]);
  const originals = forms.map(signature);
  let submitting = false;
  const dirty = () => forms.some((form, index) => signature(form) !== originals[index]);
  for (const form of forms) form.addEventListener('submit', event => { if (!event.defaultPrevented) submitting = true; });
  window.addEventListener('beforeunload', event => {
    if (!submitting && dirty()) { event.preventDefault(); event.returnValue = ''; }
  });
  setupDrydocks({act: action => {
    if (dirty()) throw new Error('当前表单尚未保存，请先保存后再切换 Drydock。');
    return action();
  }});
}
