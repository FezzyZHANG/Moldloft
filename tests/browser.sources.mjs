// Only the disposable fixture on port 8792; restart it before this script.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {join} from 'node:path';
const {chromium} = createRequire(import.meta.url)('playwright');
const browser = await chromium.launch({channel: process.env.TICKETS_BROWSER || 'msedge', headless: true});
const page = await browser.newPage({viewport: {width: 1536, height: 960}}), errors = [];
page.on('pageerror', error => errors.push(error.message));
const base = 'http://127.0.0.1:8792';
const graph = async () => (await page.request.get(base + '/api/graph')).json();
const field = name => page.locator(`#note-form [name=${name}]`);
const wait = async (fn, label) => {
  const deadline = Date.now() + 15000;
  while (!await fn()) { if (Date.now() > deadline) throw new Error(`Timed out: ${label}`); await page.waitForTimeout(80); }
};
try {
  await page.goto(base + '/graph?note=n-fixture-evidence');
  await field('title').waitFor({state: 'visible'});
  const before = await graph();
  await page.locator('#capture-note').click();
  await field('kind').selectOption('source');
  // Capture offers a context link; remove it to test provenance direction alone.
  while (await page.locator('#note-relations button').count()) await page.locator('#note-relations button').first().click();
  assert.equal(await field('state').inputValue(), 'recorded');
  assert.deepEqual(await page.locator('#note-relation-type option').evaluateAll(xs => xs.map(x => x.value)), ['relates']);
  await field('title').fill('Source report fixture');
  await field('abstract_zh').fill('记录材料出处，保留结果的适用条件。');
  await field('abstract_en').fill('A source record, not an endorsement of its conclusions.');
  await field('body').fill('Synthetic report. Version 1. Table 2. https://example.invalid/report');
  await page.locator('#note-save').click();
  await wait(async () => (await graph()).notes.some(n => n.title === 'Source report fixture'), 'source creation');
  const source = (await graph()).notes.find(n => n.title === 'Source report fixture');
  await wait(async () => await page.locator(`#dag [data-note="${source.id}"]`).count() === 1, 'source graph node');
  assert.equal(await page.locator(`#metadata-tree [data-note="${source.id}"]`).count(), 0);
  await page.locator('#notes-tree .tree-note').filter({hasText: 'Two-tab conflict fixture'}).click();
  await page.locator('#note-relation-type').selectOption('derived_from');
  assert.deepEqual(await page.locator('#note-relation-target option').evaluateAll(xs => xs.map(x => x.value).filter(Boolean)), [`note:${source.id}`]);
  await page.locator('#note-relation-target').selectOption(`note:${source.id}`);
  await page.locator('#note-add-relation').click();
  await page.locator('#note-relation-type').selectOption('challenges');
  assert.match(await page.locator('#note-relation-help').innerText(), /不会自动否定/);
  await page.locator('#note-relation-target').selectOption('note:n-single-operator');
  await page.locator('#note-add-relation').click();
  await page.locator('#chat-input').click();
  await wait(async () => (await graph()).relations.some(r => r.type === 'challenges'), 'relation save');
  await wait(async () => await page.locator('#dag .edge.relation-derived_from').count() === 1 &&
    await page.locator('#dag .edge.relation-challenges').count() === 1, 'typed edges');
  const after = await graph();
  assert.deepEqual(after.tickets.map(t => [t.id, t.status, t.ready, t.review_notes]),
    before.tickets.map(t => [t.id, t.status, t.ready, t.review_notes]));
  assert.equal(after.notes.find(n => n.id === 'n-single-operator').state, 'adopted');
  const provenance = page.locator('#dag .edge.relation-derived_from');
  assert.equal(await provenance.getAttribute('data-source'), 'n:n-fixture-evidence');
  assert.equal(await provenance.getAttribute('data-target'), `n:${source.id}`);
  assert.equal(await provenance.locator('.edge-path').getAttribute('marker-end'), 'url(#arrow-derived_from)');
  assert.equal(await page.locator('#dag .edge.relation-challenges .edge-path').getAttribute('marker-end'), 'url(#arrow-challenges)');
  const styles = await page.locator('#dag .edge:not(.removed) .edge-path').evaluateAll(xs =>
    [...new Set(xs.map(x => `${getComputedStyle(x).strokeDasharray}/${getComputedStyle(x).strokeWidth}`))]);
  assert.equal(styles.length, 7, 'all seven relations are distinguishable without color');
  assert.match(await page.locator('#backlinks').innerText(), /证伪.*反证/);
  await page.locator('.relation-legend .relation-challenges input').uncheck();
  await wait(async () => await page.locator('#dag .edge.relation-challenges').count() === 0, 'counterevidence filter');
  assert.ok((await graph()).relations.some(r => r.type === 'challenges'));
  await page.locator('.relation-legend .relation-challenges input').check();
  await page.locator('#notes-tree .tree-note').filter({hasText: 'Source report fixture'}).click();
  assert.match(await page.locator('#backlinks').innerText(), /证据出处/);
  await page.locator('#local-graph').click();
  await page.locator('#local-depth').selectOption('1');
  await page.locator('#local-direction').selectOption('upstream');
  await wait(async () => await page.locator('#dag .node[data-note]').count() === 2, 'upstream provenance');
  await page.locator('#local-direction').selectOption('downstream');
  await wait(async () => await page.locator('#dag .node[data-note]').count() === 1, 'provenance direction');
  await page.locator('#local-graph').click();
  await page.locator('#fit').click();
  await page.screenshot({path: join(process.env.TEMP || '/tmp', 'moldloft-sources-wide.png'), fullPage: true});
  await page.setViewportSize({width: 390, height: 844});
  await wait(async () => await page.locator('#dag .edge.relation-challenges').count() === 1, 'narrow graph');
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await page.screenshot({path: join(process.env.TEMP || '/tmp', 'moldloft-sources-narrow.png'), fullPage: true});
  assert.deepEqual(errors, []);
  console.log('Source creation, provenance targets, counterevidence, filters, traversal and responsive rendering passed.');
} finally { await browser.close(); }
