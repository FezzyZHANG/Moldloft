// Use only the isolated fixture server on port 8792; graph reads are synthetic.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {join} from 'node:path';
const {chromium} = createRequire(import.meta.url)('playwright');
const browser = await chromium.launch({channel: process.env.TICKETS_BROWSER || 'msedge', headless: true});
const page = await browser.newPage({viewport: {width: 1536, height: 960}});
page.setDefaultTimeout(15000);
const errors = [], writes = [];
page.on('pageerror', error => errors.push(error.message));
page.on('request', request => { if (!['GET', 'HEAD'].includes(request.method())) writes.push(request.url()); });
const base = 'http://127.0.0.1:8792';
const graph = {drydock_id: 'default', revision: 1, notes_revision: 'local-fixture', notes_errors: [], links: [],
  tickets: Array.from({length: 8}, (_, i) => ({id: i + 1, parent_id: null, group_name: 'Directional fixture',
    title: `Task ${i + 1}`, title_zh: '', description: '', tech_notes: '', status: 'open', priority: 'medium', version: 1})),
  edges: [[1, 2], [2, 3], [3, 4], [4, 5], [5, 6], [2, 7], [8, 4]].map(([blocker_id, blocked_id]) => ({blocker_id, blocked_id})),
  notes: [{id: 'n-premise', kind: 'assumption', state: 'proposed', title: 'Upstream premise',
    abstract_zh: '用于方向遍历验证的前提。', abstract_en: 'A premise for directional traversal.', body: 'Synthetic fixture.', relations: [], version: 'fixture'}],
  relations: [{source: 'note:n-premise', target: 'ticket:3', type: 'assumes', owner: 'n-premise'}],
};
const expectNodes = async expected => {
  await page.waitForFunction(expected => {
    const keys = [...document.querySelectorAll('#dag .node[data-node-key]')].map(n => n.dataset.nodeKey).filter(k => !k.startsWith('g:')).sort();
    return document.querySelector('#layout-status').textContent === '' && JSON.stringify(keys) === JSON.stringify(expected);
  }, [...expected].sort());
};
const direction = page.getByRole('combobox', {name: '局部图谱方向'});
try {
  await page.route('**/api/graph*', route => route.fulfill({json: graph}));
  await page.goto(base + '/graph?ticket=3');
  await expectNodes(['n:n-premise', ...graph.tickets.map(t => `t:${t.id}`)]);
  assert.equal(await direction.inputValue(), 'both');
  await page.locator('#local-graph').click();
  await expectNodes(['n:n-premise', 't:2', 't:3', 't:4']);
  await page.locator('#local-depth').selectOption('3');
  await expectNodes(['n:n-premise', 't:1', 't:2', 't:3', 't:4', 't:5', 't:6']);
  await direction.selectOption('upstream');
  await expectNodes(['n:n-premise', 't:1', 't:2', 't:3']);
  assert.match(await page.locator('#scope-label').innerText(), /#3 · 上游 · 3 层/);
  await direction.selectOption('downstream');
  await expectNodes(['t:3', 't:4', 't:5', 't:6']);
  assert.equal(await page.locator('#dag [data-ticket="3"]').getAttribute('class').then(c => c.includes('selected')), true);
  await page.locator('#local-depth').selectOption('1');
  await expectNodes(['t:3', 't:4']);
  await direction.selectOption('upstream');
  await expectNodes(['n:n-premise', 't:2', 't:3']);
  await page.locator('#relation-legend input[value=assumes]').uncheck();
  await expectNodes(['t:2', 't:3']);
  await page.locator('#relation-legend input[value=assumes]').check();
  await expectNodes(['n:n-premise', 't:2', 't:3']);
  await page.locator('#show-notes').uncheck();
  await expectNodes(['t:2', 't:3']);
  await page.locator('#show-notes').check();
  await expectNodes(['n:n-premise', 't:2', 't:3']);
  await page.screenshot({path: join(process.env.TEMP, 'tickets-local-desktop.png')});

  await page.setViewportSize({width: 390, height: 844});
  await direction.selectOption('downstream');
  await expectNodes(['t:3', 't:4']);
  await direction.scrollIntoViewIfNeeded();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  const box = await direction.boundingBox();
  assert.ok(box.x >= 0 && box.x + box.width <= 391, 'direction control stays within the narrow viewport');
  await page.screenshot({path: join(process.env.TEMP, 'tickets-local-narrow.png')});
  await page.setViewportSize({width: 1536, height: 960});

  await page.locator('#local-graph').click();
  await expectNodes(['n:n-premise', ...graph.tickets.map(t => `t:${t.id}`)]);
  assert.equal(await page.locator('#scope-label').innerText(), '全局');
  await page.locator('#notes-tree .tree-note').click();
  await page.locator('#local-graph').click();
  await expectNodes(['n:n-premise', 't:3']);
  await direction.selectOption('upstream');
  await expectNodes(['n:n-premise']);
  await direction.selectOption('both');
  await page.locator('#local-depth').selectOption('3');
  await expectNodes(['n:n-premise', 't:3', 't:4', 't:5']);
  assert.match(await page.locator('#scope-label').innerText(), /当前笔记 · 双向 · 3 层/);
  assert.deepEqual(errors, []);
  assert.deepEqual(writes, [], 'changing the local view never writes data');
  console.log('Directional local graph passed: ticket/note focus, independent directions, depth, filters, selection, global view and narrow controls.');
} catch (error) {
  console.error({errors, nodes: await page.locator('#dag .node').evaluateAll(nodes => nodes.map(n => n.dataset.nodeKey)),
    notice: await page.locator('#notice').textContent(), layout: await page.locator('#layout-status').textContent()});
  await page.screenshot({path: join(process.env.TEMP, 'tickets-local-failure.png'), fullPage: true});
  throw error;
} finally { await browser.close(); }
