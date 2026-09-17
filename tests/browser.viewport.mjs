// Run only against the disposable browser_server. Real SSE writes use its temporary DB.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {largeFixture} from './layout-fixtures.mjs';
const {chromium} = createRequire(import.meta.url)('playwright');
const browser = await chromium.launch({channel: process.env.TICKETS_BROWSER || 'msedge', headless: true});
const page = await browser.newPage({viewport: {width: 1536, height: 960}}), errors = [];
page.on('pageerror', e => errors.push(e.message));
const base = 'http://127.0.0.1:8792';
const graph = async () => (await page.request.get(base + '/api/graph')).json();
const wait = async (fn, label) => {
  const end = Date.now() + 20000;
  while (!await fn()) { if (Date.now() > end) throw new Error(`Timed out: ${label}`); await page.waitForTimeout(80); }
};
const screen = () => page.locator('#dag [data-ticket="3"] > rect').evaluate(n => {
  const b = n.getBoundingClientRect(); return {x: b.x, y: b.y, scale: n.getScreenCTM().a};
});
const stable = (a, b) => { for (const key of ['x', 'y', 'scale']) assert.ok(Math.abs(a[key] - b[key]) < .01, `${key}: ${a[key]} -> ${b[key]}`); };
try {
  await page.goto(base + '/graph?ticket=3');
  await wait(async () => await page.locator('#dag .node').count() > 10 && await page.locator('#layout-status').innerText() === '', 'initial layout');
  await page.evaluate(async () => {
    const {Diagram} = await import('/static/canvas.js'), render = Diagram.prototype.render;
    window.fixtureRenders = 0; window.fixtureComputes = 0;
    Diagram.prototype.render = async function(...args) {
      window.fixtureDiagram = this;
      if (!this.fixtureWrapped) { this.fixtureWrapped = true; const layout = this.elk.layout.bind(this.elk);
        this.elk.layout = (...a) => { window.fixtureComputes++; return layout(...a); }; }
      await render.apply(this, args); window.fixtureRenders++;
    };
  });
  await page.locator('#zoom-in').click();
  await page.locator('#dag').focus(); await page.keyboard.press('ArrowRight');
  const before = await screen();
  let remote = (await graph()).tickets.find(t => t.id === 9);
  assert.equal((await page.request.patch(base + '/api/tickets/9', {data: {fields: {title: 'Unrelated title updated through SSE'},
    version: remote.version, request_id: crypto.randomUUID()}})).status(), 200);
  await wait(() => page.evaluate(() => window.fixtureRenders >= 1), 'ticket SSE');
  stable(before, await screen());
  assert.equal(await page.evaluate(() => window.fixtureComputes), 0, 'text-only refresh reuses geometry');
  const note = (await graph()).notes.find(n => n.id === 'n-merge-option');
  assert.equal((await page.request.put(base + '/api/notes/' + note.id, {data: {fields: {abstract_zh: '无关摘要修改保持浏览位置。'}, version: note.version}})).status(), 200);
  await wait(() => page.evaluate(() => window.fixtureRenders >= 2), 'note SSE');
  stable(before, await screen());
  assert.equal(await page.evaluate(() => window.fixtureComputes), 0);

  const snapshot = await graph(), renders = await page.evaluate(() => window.fixtureRenders);
  assert.equal((await page.request.post(base + '/api/tickets', {data: {fields: {title: 'New unrelated component', group_name: 'Aardvark'},
    revision: snapshot.revision, request_id: crypto.randomUUID()}})).status(), 200);
  await wait(() => page.evaluate(n => window.fixtureRenders > n, renders), 'structural SSE');
  stable(before, await screen());
  assert.ok(await page.evaluate(() => window.fixtureComputes) > 0);

  await page.locator('#collapse-groups').click();
  await wait(async () => await page.locator('#dag [data-ticket="3"]').count() === 0, 'collapse');
  await page.locator('#expand-all').click();
  await wait(async () => await page.locator('#dag [data-ticket="3"]').count() === 1, 'expand');
  stable(before, await screen());
  const r = await page.evaluate(() => window.fixtureRenders);
  await page.locator('#search').fill('Build graph API');
  await wait(() => page.evaluate(n => window.fixtureRenders > n, r), 'filter');
  stable(before, await screen());
  await page.locator('#clear-filter').click();
  await wait(async () => await page.locator('#dag [data-ticket="9"]').count() === 1, 'clear filter');
  stable(before, await screen());

  // Delay layout resolution to prove pan/zoom occurring during layout is retained too.
  await page.evaluate(async () => {
    const d = window.fixtureDiagram, engine = d.elk, actual = engine.layout.bind(engine);
    const gate = new Promise(resolve => { window.finishFixtureLayout = resolve; });
    engine.layout = async input => { await gate; return actual(input); };
    window.restoreFixtureEngine = () => { engine.layout = actual; };
    d.geometry = null;
    window.pendingFixtureLayout = d.render(await (await fetch('/api/graph')).json(), new Set(), {});
  });
  await wait(() => page.evaluate(() => !!window.finishFixtureLayout), 'pending layout');
  await page.locator('#zoom-out').click(); await page.locator('#dag').focus(); await page.keyboard.press('ArrowDown');
  const panned = await screen();
  await page.evaluate(async () => { window.finishFixtureLayout(); await window.pendingFixtureLayout; window.restoreFixtureEngine(); });
  stable(panned, await screen());

  const large = largeFixture();
  await page.evaluate(graph => {
    const d = window.fixtureDiagram; window.workerFrames = 0; window.fixtureFinished = false;
    const pulse = () => { if (!window.fixtureFinished) { window.workerFrames++; requestAnimationFrame(pulse); } }; pulse();
    window.fixtureStart = performance.now();
    d.render(graph, new Set(), {}).then(() => { window.fixtureElapsed = performance.now() - window.fixtureStart; window.fixtureFinished = true; });
  }, large);
  const title = page.locator('#ticket-form input[name=title]');
  await title.fill('Typing while 500 nodes are being arranged');
  assert.equal(await title.inputValue(), 'Typing while 500 nodes are being arranged');
  await wait(() => page.evaluate(() => window.fixtureFinished), 'large worker layout');
  const performance = await page.evaluate(() => ({milliseconds: Math.round(window.fixtureElapsed), frames: window.workerFrames}));
  assert.ok(performance.frames > 1, 'main thread continues rendering while worker computes');
  assert.equal(await page.locator('#dag [data-ticket]').count(), 500);
  assert.equal(await page.locator('#dag .edge').count(), 1000);
  // Avoid saving this last synthetic draft; reload drops it only after restoring the field.
  await title.fill('Build graph API');
  await page.evaluate(async () => {
    const d = window.fixtureDiagram; d.geometry = null;
    d.elk.layout = async () => { throw new Error('Synthetic worker failure'); };
    try { await d.render({tickets: [{id: 1, parent_id: null, group_name: '', title: 'Failure fixture'}], edges: []}, new Set(), {}); } catch {}
  });
  assert.match(await page.locator('#layout-status').innerText(), /排列失败/);
  assert.equal(await page.locator('#dag [data-ticket]').count(), 500, 'last successful graph remains visible');
  await title.fill('Editor still usable after failure');
  assert.equal(await title.inputValue(), 'Editor still usable after failure');
  assert.deepEqual(errors, []);
  console.log('Viewport browser checks passed: ticket/note SSE, geometry reuse, anchored structural changes, collapse/filter, pan/zoom, responsive 500/1000, visible failure fallback.', performance);
} finally { await browser.close(); }
