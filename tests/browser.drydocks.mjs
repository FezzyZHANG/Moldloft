// Multi-graph UI/MCP regression on python -m tests.browser_server, never a live workspace.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {join} from 'node:path';
const {chromium} = createRequire(import.meta.url)('playwright');
const browser = await chromium.launch({channel: process.env.TICKETS_BROWSER || 'msedge', headless: true});
const context = await browser.newContext({viewport: {width: 1536, height: 960}});
const page = await context.newPage(), errors = [];
page.on('pageerror', error => errors.push(error.message));
const base = 'http://127.0.0.1:8792';
const graph = async id => (await page.request.get(`${base}/api/graph?drydock=${id}`)).json();
const ticket = async id => (await graph(id)).tickets.find(t => t.id === 1);
const wait = async (condition, label) => {
  const start = Date.now();
  while (!(await condition())) {
    if (Date.now() - start > 15000) throw new Error('Timed out: ' + label);
    await new Promise(resolve => setTimeout(resolve, 70));
  }
};
const currentId = () => page.locator('body').getAttribute('data-drydock-id');
const choose = async id => {
  await page.locator('#drydock-select').selectOption(id);
  await wait(async () => await currentId() === id, 'selected Drydock ' + id);
  await page.locator('#connection.online').waitFor();
};
const call = async (name, args = {}) => {
  const response = await page.request.post(base + '/mcp', {headers: {Accept: 'application/json, text/event-stream'},
    data: {jsonrpc: '2.0', id: 1, method: 'tools/call', params: {name, arguments: args}}});
  const {result} = await response.json();
  assert.ok(!result.isError, JSON.stringify(result));
  return result.structuredContent || JSON.parse(result.content[0].text);
};
const title = () => page.locator('#ticket-form input[name=title]');
try {
  await page.goto(base + '/graph');
  await page.locator('#connection.online').waitFor();
  const original = await graph('default');

  // A lost create acknowledgement retries one ID and retains the name in the dialog.
  let createCalls = 0;
  await page.route('**/api/drydocks', async route => {
    if (route.request().method() !== 'POST' || createCalls++) return route.continue();
    await route.fetch(); await route.abort('failed');
  });
  await page.locator('#drydock-create').click();
  await page.locator('#drydock-name').fill('Browser Drydock');
  await page.locator('#drydock-submit').click();
  await page.locator('#drydock-form-error').waitFor({state: 'visible'});
  assert.equal(await page.locator('#drydock-name').inputValue(), 'Browser Drydock');
  await page.locator('#drydock-submit').click();
  await wait(async () => await currentId() !== 'default', 'created graph');
  await page.locator('#connection.online').waitFor();
  await page.unroute('**/api/drydocks');
  const dock = await currentId();
  assert.equal((await call('list_drydocks')).items.filter(d => d.name === 'Browser Drydock').length, 1);
  assert.equal((await graph(dock)).tickets.length, 0);

  await page.locator('#new-ticket').click();
  await title().fill('Independent work');
  await page.locator('#save-now').click();
  await wait(async () => (await ticket(dock))?.title === 'Independent work', 'create scoped ticket');
  await wait(async () => await page.locator('#save-state').innerText() === '已保存', 'create acknowledgement');

  // MCP arrivals and review are visible only on the owning page/SSE stream.
  const other = await context.newPage();
  other.on('pageerror', error => errors.push(error.message));
  await other.goto(base + '/graph?ticket=1');
  await other.locator('#ticket-form input[name=title]').waitFor({state: 'visible'});
  const captured = (await call('get_ticket', {ticket_id: 1, drydock_id: dock})).basis;
  await call('propose_changes', {drydock_id: dock, operations: [
    {op: 'update', ticket_id: 1, fields: {description: 'Proposal in a named graph'}}],
    basis: captured, base_revision: captured.revision, notes_revision: captured.notes_revision,
    summary: 'Drydock review', rationale: 'Review only this graph', origin: 'free', request_id: crypto.randomUUID()});
  await page.locator('.tree-proposal').first().waitFor();
  assert.equal(await other.locator('.tree-proposal').count(), 0);
  await page.locator('.tree-proposal').first().click();
  await page.locator('#review-apply').click();
  await wait(async () => (await ticket(dock)).description === 'Proposal in a named graph', 'scoped review');
  await page.goto(`${base}/graph?drydock=${dock}&ticket=1`);
  await title().waitFor({state: 'visible'});
  await page.locator('#chat-input').fill('Propose a fixture change');
  await page.locator('#send-chat').click();
  await page.locator('#apply-proposal:not([disabled])').waitFor({state: 'visible'});
  await page.locator('#apply-proposal').click();
  await wait(async () => (await ticket(dock)).title === 'Reviewed API contract', 'scoped Copilot apply');
  assert.equal((await graph(dock)).tickets.length, 2);
  assert.deepEqual(await graph('default'), original);
  assert.equal(await other.locator('#ticket-form input[name=title]').inputValue(), original.tickets[0].title);

  // Switching waits for a save acknowledgement; the PATCH keeps the old graph URL.
  let release, intercepted = false;
  const gate = new Promise(resolve => { release = resolve; });
  const patchPath = `**/api/tickets/1?drydock=${dock}`;
  await page.route(patchPath, async route => { intercepted = true; await gate; await route.continue(); });
  await title().fill('Saved before switch');
  await page.locator('#drydock-select').selectOption('default');
  await wait(() => intercepted, 'pending scoped save');
  assert.equal(await currentId(), dock);
  assert.equal(await page.locator('#drydock-select').inputValue(), dock);
  release();
  await wait(async () => await currentId() === 'default', 'switch after acknowledgement');
  await page.unroute(patchPath);
  assert.equal((await ticket(dock)).title, 'Saved before switch');
  assert.equal(await page.locator('#messages .message').count(), 0);
  assert.equal(await page.locator('#proposal').isHidden(), true);

  // Failed writes and IME composition keep the draft and selection in place.
  await page.goto(`${base}/graph?drydock=${dock}&ticket=1`);
  await title().waitFor({state: 'visible'});
  await page.route(patchPath, route => route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({message: 'Fixture save unavailable'})}));
  await title().fill('Retained failed draft');
  await page.locator('#drydock-select').selectOption('default');
  await wait(async () => (await page.locator('#notice').innerText()).includes('Fixture save unavailable'), 'visible failure');
  assert.equal(await currentId(), dock);
  assert.equal(await title().inputValue(), 'Retained failed draft');
  assert.equal(await page.locator('#drydock-select').inputValue(), dock);
  await page.unroute(patchPath);
  await page.locator('#save-now').click();
  await wait(async () => (await ticket(dock)).title === 'Retained failed draft', 'retry retained save');
  await title().dispatchEvent('compositionstart');
  await title().fill('输入法完成后的标题');
  await page.locator('#drydock-select').selectOption('default');
  await wait(async () => (await page.locator('#notice').innerText()).includes('完成输入'), 'IME holds switch');
  assert.equal(await currentId(), dock);
  assert.equal((await ticket(dock)).title, 'Retained failed draft');
  await title().dispatchEvent('compositionend');
  await choose('default');
  assert.equal((await ticket(dock)).title, '输入法完成后的标题');

  // Notes use the same pinned API; switching flushes persisted notes but holds new drafts.
  await page.request.put(`${base}/api/notes/n-dock?drydock=${dock}`, {data: {fields: {title: 'Drydock note'}}});
  await page.goto(`${base}/graph?drydock=${dock}&note=n-dock`);
  const body = page.locator('#note-form textarea[name=body]');
  await body.waitFor({state: 'visible'}); await body.fill('Saved in its own Markdown notebook');
  await choose('default');
  assert.equal((await graph(dock)).notes[0].body, 'Saved in its own Markdown notebook');
  await choose(dock);
  await page.locator('#new-note').click();
  await page.locator('#note-form input[name=title]').fill('Uncreated note');
  await page.locator('#drydock-select').selectOption('default');
  await wait(async () => (await page.locator('#notice').innerText()).includes('新笔记尚未创建'), 'hold new note');
  assert.equal(await currentId(), dock);
  await page.locator('#note-cancel').click();

  // A concurrent rename preserves typed input and refreshes only catalog metadata.
  await page.locator('#drydock-rename').click();
  const row = (await call('list_drydocks')).items.find(d => d.id === dock);
  await page.request.patch(`${base}/api/drydocks/${dock}`, {data: {name: 'Changed elsewhere', version: row.version}});
  await page.locator('#drydock-name').fill('Design & build <fixture>');
  await page.locator('#drydock-submit').click();
  await page.locator('#drydock-form-error').waitFor({state: 'visible'});
  assert.equal(await page.locator('#drydock-name').inputValue(), 'Design & build <fixture>');
  await page.locator('#drydock-submit').click();
  await page.locator('#drydock-dialog').waitFor({state: 'hidden'});
  assert.equal(await page.locator('#drydock-select option:checked').innerText(), 'Design & build <fixture>');
  assert.ok((await page.title()).includes('Design & build <fixture>'));

  // Older list/detail links, writes and redirects remain in the same graph.
  await page.locator('nav a').click();
  await page.locator(`a[href="/tickets/1?drydock=${dock}"]`).click();
  const legacyTitle = page.locator('form[method=post] input[name=title]');
  await legacyTitle.fill('Saved through the scoped form');
  await page.locator('#drydock-select').selectOption('default');
  await page.locator('#drydock-error').waitFor({state: 'visible'});
  assert.equal(await currentId(), dock);
  await page.locator('form[method=post]').first().getByRole('button', {name: '保存', exact: true}).click();
  await wait(async () => (await ticket(dock)).title === 'Saved through the scoped form', 'legacy scoped save');
  assert.equal(new URL(page.url()).searchParams.get('drydock'), dock);
  await page.getByText('依赖图 →', {exact: true}).click();
  await page.locator('#connection.online').waitFor();
  await wait(async () => await page.locator('#dag .node').count() >= 3 && await page.locator('#layout-status').innerText() === '', 'desktop layout complete');
  await page.screenshot({path: join(process.env.TEMP || '/tmp', 'moldloft-drydock-desktop.png'), fullPage: true});
  await page.setViewportSize({width: 390, height: 844});
  await wait(async () => await page.locator('#layout-status').innerText() === '', 'mobile layout complete');
  await page.screenshot({path: join(process.env.TEMP || '/tmp', 'moldloft-drydock-mobile.png'), fullPage: true});
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'no mobile horizontal overflow');
  await page.locator('#drydock-rename').click();
  await page.screenshot({path: join(process.env.TEMP || '/tmp', 'moldloft-drydock-dialog.png')});
  await page.locator('#drydock-cancel').click();
  // A vanished destination leaves the old page, selector and SSE usable.
  await page.route('**/api/graph', route => route.fulfill({status: 503,
    contentType: 'application/json', body: JSON.stringify({message: 'Destination unavailable'})}));
  await page.locator('#drydock-select').selectOption('default');
  await wait(async () => (await page.locator('#notice').innerText()).includes('Destination unavailable'), 'unavailable destination');
  assert.equal(await currentId(), dock);
  assert.equal(await page.locator('#drydock-select').inputValue(), dock);
  assert.equal(await page.locator('#connection.online').count(), 1);
  await page.unroute('**/api/graph');
  assert.deepEqual(await graph('default'), original, 'every scoped action preserves the default graph');
  assert.deepEqual(errors, []);
  await other.close();
  console.log('Drydock UI/MCP, scoped SSE/Copilot/forms, retries, drafts, IME, notes and mobile: PASS');
} finally { await browser.close(); }
