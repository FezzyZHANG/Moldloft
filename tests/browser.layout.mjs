// Isolated browser fixture only. Output is regenerable; never use the live queue.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdir, writeFile, readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fixtures, observations} from './layout-fixtures.mjs';
const {chromium} = createRequire(import.meta.url)('playwright');
const output = process.env.TICKETS_LAYOUT_OUTPUT || join(process.env.TEMP || '/tmp', 'tickets-layout-33');
await mkdir(output, {recursive: true});
const phase = process.argv.includes('--before') ? 'before' : 'after';
const browser = await chromium.launch({channel: process.env.TICKETS_BROWSER || 'msedge', headless: true});
const page = await browser.newPage({viewport: {width: 1536, height: 960}});
const errors = []; page.on('pageerror', error => errors.push(error.message));
const base = 'http://127.0.0.1:8792';
const results = {};
try {
  if (phase === 'before') {
    const baseline = JSON.parse(await readFile(new URL('./layout-before.json', import.meta.url), 'utf8'));
    for (const [file, body] of Object.entries(baseline.files)) await page.route(`**/static/${file}`, route => route.fulfill({body, contentType: 'text/javascript'}));
  }
  for (const [name, factory] of Object.entries(fixtures)) {
    const graph = {revision: 1, notes_revision: 'fixture', notes: [], relations: [], links: [], notes_errors: [], ...factory()};
    await page.route('**/api/graph', route => route.fulfill({json: graph}));
    await page.goto(base + '/graph');
    await page.waitForFunction(() => document.querySelector('#dag .node') && document.querySelector('#layout-status').textContent === '');
    const result = await page.evaluate(async graph => {
      const {project} = await import('/static/layout.js');
      const elk = new window.ELK({workerUrl: '/static/vendor/elk/elk-worker.min.js'});
      try {
        if (document.querySelector('#dag').dataset.layoutStrategy) {
          const {computeLayout} = await import('/static/layout-strategies.js');
          return await computeLayout(project(graph), {engine: elk});
        }
        return await elk.layout(project(graph));
      } finally { elk.terminateWorker(); }
    }, graph);
    results[name] = observations(result);
    await page.locator('#fit').click();
    await page.screenshot({path: join(output, `${phase}-${name}-desktop.png`)});
    if (name === 'mixed') {
      await page.locator('.tree-ticket').filter({hasText: /^#2 /}).click();
      await page.waitForFunction(() => document.querySelector('#layout-status').textContent === '');
      await page.screenshot({path: join(output, `${phase}-selected-desktop.png`)});
      await page.locator('#local-graph').click();
      await page.waitForFunction(() => document.querySelector('#layout-status').textContent === '');
      await page.screenshot({path: join(output, `${phase}-local-desktop.png`)});
      await page.setViewportSize({width: 390, height: 844});
      await page.locator('#dag').scrollIntoViewIfNeeded();
      if (phase === 'after') await page.waitForFunction(() => {
        const dag = document.querySelector('#dag'), node = dag.querySelector('[data-ticket="2"] > rect');
        const viewport = dag.getBoundingClientRect(), box = node.getBoundingClientRect(), scale = node.getScreenCTM().a;
        return scale >= .65 && box.x >= viewport.x && box.y >= viewport.y &&
          box.x + 244 * scale <= viewport.right && box.y + 112 * scale <= viewport.bottom;
      });
      await page.screenshot({path: join(output, `${phase}-local-narrow.png`)});
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await page.locator('#local-graph').click();
      await page.waitForFunction(() => document.querySelector('#layout-status').textContent === '');
      await page.locator('#fit').click();
      await page.screenshot({path: join(output, `${phase}-mixed-narrow.png`)});
      await page.setViewportSize({width: 1536, height: 960});
    }
    await page.unroute('**/api/graph');
  }
  assert.deepEqual(errors, []);
  await writeFile(join(output, `${phase}-metrics.json`), JSON.stringify(results, null, 2) + '\n');
  console.log(JSON.stringify({phase, output, results}, null, 2));
} finally { await browser.close(); }
