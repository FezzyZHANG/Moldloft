// Use only the disposable fixture server on port 8792; all writes are synthetic.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {join} from 'node:path';
const {chromium} = createRequire(import.meta.url)('playwright');
const browser = await chromium.launch({channel: process.env.TICKETS_BROWSER || 'msedge', headless: true});
const page = await browser.newPage({viewport: {width: 1536, height: 960}}), errors = [];
page.on('pageerror', error => errors.push(error.message));
const base = 'http://127.0.0.1:8792';
const graph = async (dock = 'default') => (await page.request.get(`${base}/api/graph?drydock=${dock}`)).json();
const wait = async (condition, label) => {
  const deadline = Date.now() + 15000;
  while (!await condition()) {
    if (Date.now() > deadline) throw new Error(`Timed out: ${label}`);
    await page.waitForTimeout(70);
  }
};
const field = name => page.locator(`#note-form [name=${name}]`);
const metadata = () => page.locator('#metadata-tree .tree-note');
const current = async id => (await graph()).notes.find(n => n.id === id);
const settled = () => page.locator('#save-state').innerText().then(value => value === '已保存');
try {
  await page.goto(base + '/graph?note=n-save-question');
  await field('title').waitFor({state: 'visible'});
  const original = await graph();
  const originalQuestion = original.notes.find(n => n.id === 'n-save-question');
  await wait(async () => await page.locator('#dag .node[data-note]').count() === original.notes.length, 'initial graph');
  await page.locator('#new-metadata').click();
  assert.equal(await field('kind').inputValue(), 'metadata');
  assert.equal(await field('state').inputValue(), 'recorded');
  assert.ok(await page.locator('#local-graph').isDisabled());
  await field('title').fill('Review overview fixture');
  await field('body').fill('An authored overview, with navigation that must not join unrelated work.');
  await field('abstract_zh').fill('总览入口，保留每个研究方向的独立性。');
  await page.locator('#note-relation-target').selectOption('ticket:3');
  await page.locator('#note-add-relation').click();
  assert.equal((await graph()).notes.length, original.notes.length, 'new drafts require explicit creation');
  await page.locator('#note-save').click();
  await wait(async () => await metadata().count() === 1 && await settled(), 'metadata creation acknowledgement');
  const index = (await graph()).notes.find(n => n.kind === 'metadata');
  assert.equal(await page.locator('#notes-tree .tree-note').count(), original.notes.length);
  assert.equal(await page.locator('#dag .node[data-note]').count(), original.notes.length);
  assert.equal(await page.locator(`#dag [data-note="${index.id}"]`).count(), 0);
  assert.ok(await page.locator('#local-graph').isDisabled());
  assert.match(await page.locator('#backlinks').innerText(), /说明导航/);
  assert.match(await page.locator('#note-relations').innerText(), /说明导航/);
  assert.match(await page.locator('#note-relation-help').innerText(), /不进入图谱/);
  const chips = await page.locator('#chat-context').innerText();
  assert.match(chips, /Review overview fixture/);
  assert.ok(!chips.includes('#3'), 'selecting metadata does not implicitly attach indexed work');
  await page.locator('#backlinks button').click();
  await page.locator('#ticket-form').waitFor({state: 'visible'});
  assert.ok(!(await page.locator('#chat-context').innerText()).includes('Review overview fixture'));
  await page.locator('#local-graph').click();
  await page.locator('#local-depth').selectOption('3');
  await metadata().click();
  await wait(async () => await page.locator('#local-graph').isDisabled(), 'metadata exits local traversal');
  assert.equal(await page.locator('#local-graph').getAttribute('aria-pressed'), 'false');

  // Conversion retains the same note, including incoming option links as navigation.
  await page.locator('#notes-tree .tree-note').filter({hasText: originalQuestion.title}).click();
  await field('kind').selectOption('metadata');
  await page.locator('#chat-input').click();
  await wait(async () => (await current(originalQuestion.id)).kind === 'metadata' && await settled(), 'conversion acknowledgement');
  const converted = await current(originalQuestion.id);
  for (const key of ['id', 'title', 'body', 'abstract_zh', 'abstract_en', 'relations']) assert.deepEqual(converted[key], originalQuestion[key]);
  await wait(async () => await metadata().count() === 2 && await page.locator('#dag .node[data-note]').count() === original.notes.length - 1, 'converted projection');
  assert.match(await page.locator('#backlinks').innerText(), /说明导航/);
  assert.equal(await page.locator('#dag .edge.relation-option').count(), 0, 'incoming links to metadata stay outside layout');

  // A concurrent author keeps the browser draft visible until explicit reconciliation.
  await field('body').fill('Browser metadata draft');
  await page.request.put(`${base}/api/notes/${converted.id}`, {data: {version: converted.version, fields: {body: 'External metadata text'}}});
  await page.locator('#note-conflict').waitFor({state: 'visible'});
  assert.equal(await field('body').inputValue(), 'Browser metadata draft');
  await page.locator('#note-use-server').click();
  await wait(async () => await field('body').inputValue() === 'External metadata text', 'explicit conflict resolution');
  await field('kind').selectOption('question');
  await page.locator('#chat-input').click();
  await wait(async () => (await current(converted.id)).kind === 'question' && await metadata().count() === 1, 'ordinary note restored');
  await wait(async () => await page.locator('#dag .edge.relation-option').count() === 2, 'typed edges restored');

  await page.locator('#search').fill('总览入口');
  await wait(async () => await metadata().count() === 1 && await page.locator('#notes-tree .tree-note').count() === 0, 'metadata abstract search');
  await wait(async () => await page.locator('#dag .node[data-note]').count() === 0, 'metadata stays excluded during search layout');
  await page.locator('#clear-filter').click();
  await metadata().click();

  // Every Drydock has its own list. Equal note IDs do not reuse text or selection.
  const created = await page.request.post(base + '/api/drydocks', {data: {name: 'Metadata fixture', request_id: crypto.randomUUID()}});
  assert.equal(created.status(), 200);
  const dock = (await created.json()).id;
  const response = await page.request.put(`${base}/api/notes/${index.id}?drydock=${dock}`, {data: {
    fields: {title: 'Independent overview fixture', kind: 'metadata', state: 'recorded', body: 'A different Drydock.'}}});
  assert.equal(response.status(), 200);
  await page.reload();
  await page.locator('#connection.online').waitFor();
  await page.locator('#drydock-select').selectOption(dock);
  await wait(async () => await page.locator('body').getAttribute('data-drydock-id') === dock && await metadata().count() === 1, 'scoped metadata list');
  assert.equal(await metadata().innerText(), '◇ Independent overview fixture\n摘要待补充');
  assert.ok(!await field('title').isVisible(), 'selection is cleared on a Drydock switch');
  await metadata().click();
  assert.equal(await field('body').inputValue(), 'A different Drydock.');
  await field('body').fill('Saved in the independent Drydock.');
  await page.locator('#drydock-select').selectOption('default');
  await wait(async () => await page.locator('body').getAttribute('data-drydock-id') === 'default', 'save before switching back');
  assert.equal((await graph(dock)).notes[0].body, 'Saved in the independent Drydock.');
  assert.equal((await current(index.id)).body, index.body);
  await metadata().click();
  await wait(async () => await page.locator('#layout-status').innerText() === '' &&
    await page.locator('#dag .node[data-note]').count() === original.notes.length, 'final global layout');
  await page.locator('#fit').click();
  await page.screenshot({path: join(process.env.TEMP, 'tickets-metadata-overview.png')});
  await page.setViewportSize({width: 390, height: 844});
  await metadata().scrollIntoViewIfNeeded();
  await page.screenshot({path: join(process.env.TEMP, 'tickets-metadata-mobile.png'), fullPage: true});
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  assert.deepEqual(errors, []);
  console.log('Metadata browser checks passed: creation, navigation, graph exclusion, conversion, conflicts, search, Drydock isolation and mobile.');
} catch (error) {
  await page.screenshot({path: join(process.env.TEMP, 'tickets-metadata-failure.png'), fullPage: true});
  throw error;
} finally { await browser.close(); }
