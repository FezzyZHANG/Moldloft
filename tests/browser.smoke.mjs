// Run against python -m tests.browser_server; never point at a real DB.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {join} from 'node:path';
const {chromium} = createRequire(import.meta.url)('playwright');
const browser = await chromium.launch({channel: process.env.TICKETS_BROWSER || 'msedge', headless: true});
const context = await browser.newContext({viewport: {width: 1536, height: 960}});
const page = await context.newPage(), failures = [];
page.on('pageerror', error => failures.push(error.message));
let saves = 0;
page.on('request', request => { if (request.method() === 'PATCH') saves++; });
const base = 'http://127.0.0.1:8792';
const graph = async () => (await page.request.get(base + '/api/graph')).json();
const current = async () => (await graph()).tickets.find(t => t.id === 3);
const wait = async (condition, label) => {
  const start = Date.now();
  while (!(await condition())) {
    if (Date.now() - start > 15000) throw new Error('Timed out: ' + label);
    await new Promise(resolve => setTimeout(resolve, 80));
  }
};
try {
  await page.goto(base + '/graph?ticket=3');
  await page.locator('#ticket-form input[name=title]').waitFor({state: 'visible'});
  await wait(() => page.locator('#dag .node').count().then(n => n >= 11), 'ELK layout');
  assert.equal(await page.locator('#dag .edge.relation-requires').count(), 6);
  await page.screenshot({path: join(process.env.TEMP || '/tmp', 'tickets-dag-initial.png')});
  const title = page.locator('#ticket-form input[name=title]');
  const original = (await current()).title;
  await title.fill('Attention save fixture');
  await page.locator('#ticket-form textarea[name=description]').click();
  await page.waitForTimeout(350);
  assert.equal((await current()).title, original);
  assert.equal(saves, 0, 'field-to-field focus must not save');
  await page.locator('#chat-input').click();
  await wait(async () => (await current()).title === 'Attention save fixture', 'attention save');
  await wait(async () => (await page.locator('#save-state').innerText()) === '已保存', 'ack');
  assert.equal(saves, 1, 'pointer and focus events must coalesce');

  const second = await context.newPage();
  await second.goto(base + '/graph?ticket=3');
  await second.locator('#ticket-form input[name=title]').waitFor({state: 'visible'});
  await page.bringToFront();
  await title.fill('Local conflict fixture');
  const remote = await current();
  const result = await page.request.patch(base + '/api/tickets/3', {data: {fields: {title: 'Remote fixture'},
    version: remote.version, request_id: crypto.randomUUID()}});
  assert.equal(result.status(), 200);
  await wait(() => page.locator('#conflict').isVisible(), 'SSE conflict detection');
  assert.equal(await title.inputValue(), 'Local conflict fixture');
  await page.locator('#keep-draft').click();
  await wait(async () => (await current()).title === 'Local conflict fixture', 'explicit conflict resolution');
  await wait(async () => (await second.locator('#ticket-form input[name=title]').inputValue()) === 'Local conflict fixture', 'second page SSE');
  await second.close();

  // Closing an SSE response exercises reconnect; Chromium's offline emulation
  // does not consistently tear down an already-open localhost event stream.
  await page.route('**/api/events', route => route.fulfill({status: 200,
    contentType: 'text/event-stream', body: ': fixture connection closed\n\n'}));
  await page.reload();
  await title.waitFor({state: 'visible'});
  await wait(async () => (await page.locator('#connection').innerText()).includes('重连中'), 'SSE disconnect');
  const disconnected = await (await fetch(base + '/api/graph')).json();
  const disconnectedTicket = disconnected.tickets.find(t => t.id === 3);
  const offlineWrite = await fetch(base + '/api/tickets/3', {method: 'PATCH', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({fields: {tech_notes: 'Changed while event stream disconnected'}, version: disconnectedTicket.version, request_id: crypto.randomUUID()})});
  assert.equal(offlineWrite.status, 200);
  await page.unroute('**/api/events');
  await wait(async () => (await page.locator('#ticket-form textarea[name=tech_notes]').inputValue()) === 'Changed while event stream disconnected', 'SSE reconnect snapshot');

  await title.fill('Composition fixture');
  await title.dispatchEvent('compositionstart');
  const beforeIME = saves;
  await page.locator('#chat-input').click();
  await page.waitForTimeout(250);
  assert.equal(saves, beforeIME, 'composition must finish before saving');
  await title.dispatchEvent('compositionend');
  await wait(async () => (await current()).title === 'Composition fixture', 'composition-end save');
  await wait(async () => (await page.locator('#save-state').innerText()) === '已保存', 'IME ack');

  await page.route('**/api/tickets/3', route => route.abort('failed'));
  await title.fill('Retry retained fixture');
  await page.locator('#chat-input').click();
  await wait(async () => (await page.locator('#save-state').innerText()) === '保存未完成', 'failed save');
  assert.equal(await title.inputValue(), 'Retry retained fixture');
  await page.unroute('**/api/tickets/3');
  await page.keyboard.press('Control+s');
  await wait(async () => (await current()).title === 'Retry retained fixture', 'retry saved');
  await wait(async () => (await page.locator('#save-state').innerText()) === '已保存', 'retry ack');
  if (await page.locator('#notice').isVisible()) await page.locator('#notice').click();

  const before = await graph();
  await page.locator('#chat-input').fill('Review the selected API ticket and add a child task.');
  await page.locator('#send-chat').click();
  await wait(() => page.locator('#proposal').isVisible(), 'Copilot proposal');
  assert.deepEqual(await graph(), before, 'preview must not write');
  assert.equal(await title.inputValue(), 'Retry retained fixture', 'preview must not replace the editor draft');
  await wait(() => page.locator('#dag [data-ticket="new:review"]').count().then(n => n === 1), 'preview graph');
  await page.screenshot({path: join(process.env.TEMP || '/tmp', 'tickets-dag-proposal.png')});
  await page.locator('#apply-proposal').click();
  await wait(async () => (await current()).title === 'Reviewed API contract', 'applied proposal');
  assert.equal((await graph()).tickets.length, before.tickets.length + 1);
  await page.reload();
  await page.locator('#ticket-form input[name=title]').waitFor({state: 'visible'});
  assert.equal(await page.locator('#messages .message').count(), 0, 'chat must not survive reload');
  assert.equal(await page.evaluate(() => localStorage.length), 0);
  const beforeCancel = await graph();
  await page.locator('#chat-input').fill('Prepare another review.');
  await page.locator('#send-chat').click();
  await wait(() => page.locator('#stop-chat').isVisible(), 'stream started');
  await page.locator('#new-chat').click();
  await page.waitForTimeout(1000);
  assert.equal(await page.locator('#messages .message').count(), 0, 'cancelled old response must not repopulate cleared chat');
  assert.equal(await page.locator('#chat-input').inputValue(), '');
  assert.deepEqual(await graph(), beforeCancel);
  await page.locator('#chat-input').fill('Prepare a review preview.');
  await page.locator('#send-chat').click();
  await wait(() => page.locator('#proposal').isVisible(), 'second preview');
  const latest = await current();
  await page.request.patch(base + '/api/tickets/3', {data: {fields: {priority: 'low'},
    version: latest.version, request_id: crypto.randomUUID()}});
  await wait(() => page.locator('#apply-proposal').isDisabled(), 'stale proposal rejected');
  assert.match(await page.locator('#proposal-state').innerText(), /数据已变化/);
  await page.locator('#reject-proposal').click();
  await page.locator('#collapse-groups').click();
  await wait(() => page.locator('#dag [data-ticket]').count().then(n => n === 0), 'collapsed groups');
  await page.locator('#expand-all').click();
  await wait(() => page.locator('#dag [data-ticket]').count().then(n => n === 10), 'expanded graph');
  await page.emulateMedia({colorScheme: 'dark'});
  await page.screenshot({path: join(process.env.TEMP || '/tmp', 'tickets-dag-dark.png')});
  await page.setViewportSize({width: 390, height: 844});
  await page.screenshot({path: join(process.env.TEMP || '/tmp', 'tickets-dag-mobile.png'), fullPage: true});
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'mobile should not overflow horizontally');
  const beforeCreate = (await graph()).tickets.length;
  await page.locator('#new-ticket').click();
  await title.fill('Manual creation fixture');
  let releaseCreate;
  const createGate = new Promise(resolve => { releaseCreate = resolve; });
  await page.route('**/api/tickets', async route => { await createGate; await route.continue(); });
  await page.locator('#save-now').click();
  await wait(() => title.isDisabled(), 'creation locks its submitted fields until acknowledgement');
  releaseCreate();
  await wait(async () => (await graph()).tickets.length === beforeCreate + 1, 'manual creation');
  await wait(async () => (await page.locator('#save-state').innerText()) === '已保存', 'creation acknowledgement');
  assert.equal(await title.inputValue(), 'Manual creation fixture');
  assert.deepEqual(failures, []);
  console.log('Browser PASS: layout, attention/IME saves, conflict + SSE, failed-save retry, atomic preview/apply, stale/cancelled proposals, ephemeral chat, collapse, dark/mobile.');
} catch (error) {
  await page.screenshot({path: join(process.env.TEMP || '/tmp', 'tickets-dag-failure.png'), fullPage: true});
  throw error;
} finally {
  await browser.close();
}
