// In-graph proposal review against the disposable browser fixture; never the live queue.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {join} from 'node:path';
const {chromium} = createRequire(import.meta.url)('playwright');
const browser = await chromium.launch({channel: process.env.TICKETS_BROWSER || 'msedge', headless: true});
const page = await browser.newPage({viewport: {width: 1365, height: 960}});
const base = 'http://127.0.0.1:8792';
const errors = [];
page.on('pageerror', error => errors.push(error.message));
async function call(name, args) {
  const response = await page.request.post(base + '/mcp', {headers: {Accept: 'application/json, text/event-stream'},
    data: {jsonrpc: '2.0', id: 1, method: 'tools/call', params: {name, arguments: args}}});
  assert.equal(response.status(), 200);
  const {result} = await response.json();
  assert.ok(!result.isError, JSON.stringify(result));
  return result.structuredContent || JSON.parse(result.content[0].text);
}
const wait = async (condition, label) => {
  const start = Date.now();
  while (!(await condition())) {
    if (Date.now() - start > 15000) throw new Error('Timed out: ' + label);
    await new Promise(resolve => setTimeout(resolve, 80));
  }
};
try {
  await page.goto(base + '/graph?ticket=3');
  await page.getByText('演示数据', {exact: true}).waitFor();
  await wait(() => page.locator('#dag .node').count().then(n => n >= 11), 'ELK layout');
  assert.ok(await page.locator('#proposal-count').isHidden(), 'no pending count before submission');

  // A ticket-only batch arrives over MCP; the header count and grouped list follow over SSE.
  const basis = (await call('get_ticket', {ticket_id: 3})).basis;
  const pending = await call('propose_changes', {operations: [
    {op: 'update', ticket_id: 3, fields: {title: 'Reviewed through the graph', description: 'Synthetic demonstration ticket. Safe to edit or delete.\nReviewed line.'}},
    {op: 'edge_add', blocker_id: 5, blocked_id: 9}
  ], basis, base_revision: basis.revision, notes_revision: basis.notes_revision,
    summary: 'Browser proposal fixture', rationale: 'First line\nSecond line: inspect the differences before acceptance.\n<script>window.injected = true</script>',
    origin: 'free', request_id: 'browser-review-submit-20260913'});
  await wait(async () => (await page.locator('#proposal-count').innerText()).includes('1'), 'pending count over SSE');
  assert.equal(await page.locator('#proposals-tree h3').first().innerText(), 'Interface · Platform · 1');
  await page.locator('.tree-proposal').first().click();
  await page.locator('#review-form').waitFor({state: 'visible'});
  assert.ok((await page.locator('#review-check').innerText()).includes('仍可应用'));
  const lines = await page.locator('#review-sections .line').allInnerTexts();
  assert.ok(lines.some(l => l.startsWith('- title: ')), 'field-level removed line');
  assert.ok(lines.some(l => l === '+ title: "Reviewed through the graph"'), 'field-level added line');
  assert.ok(lines.includes('+ #5 -> #9'), 'edge signature line');
  assert.ok(lines.some(l => l.startsWith('@@ description')), 'prose diff marker');
  assert.equal(await page.evaluate(() => window.injected), undefined, 'rationale is text, not markup');
  await wait(() => page.locator('#dag .node.changed').count().then(n => n >= 1), 'overlay marks the changed ticket');
  assert.ok((await page.locator('#graph-hint').innerText()).includes('预览'));
  await page.screenshot({path: join(process.env.TEMP || '/tmp', 'review-desktop.png'), fullPage: true});

  // Accept with an edit and a note; the outcome records the amendment.
  await page.locator('#review-edit summary').click();
  const title = page.locator('#review-edits textarea[data-field=title]');
  await title.fill('Reviewed through the graph, trimmed');
  await page.locator('#review-note').fill('Trimmed the title.');
  await page.locator('#review-apply').click();
  await wait(async () => (await page.locator('#review-check').innerText()).includes('已应用'), 'applied outcome');
  const applied = await call('get_proposal', {proposal_id: pending.proposal_id});
  assert.equal(applied.status, 'applied');
  assert.equal(applied.result.amendments[0].fields.title.applied, 'Reviewed through the graph, trimmed');
  assert.equal(applied.result.note, 'Trimmed the title.');
  const graph = await (await page.request.get(base + '/api/graph')).json();
  assert.equal(graph.tickets.find(t => t.id === 3).title, 'Reviewed through the graph, trimmed');
  assert.ok(graph.edges.some(e => e.blocker_id === 5 && e.blocked_id === 9));
  await wait(() => page.locator('#proposal-count').isHidden(), 'count clears');

  // A note-only batch: relation lines render explicitly; rejection needs a reason the agent reads back.
  const b2 = (await call('get_note', {note_id: 'n-save-question'})).basis;
  const second = await call('propose_changes', {operations: [
    {op: 'note_update', note_id: 'n-save-question', fields: {state: 'resolved', relations: [{type: 'relates', target: 'ticket:4'}, {type: 'relates', target: 'ticket:3'}]}}
  ], basis: b2, base_revision: b2.revision, notes_revision: b2.notes_revision,
    summary: 'Browser rejection fixture', rationale: 'This one must be rejected.', origin: 'resolve', request_id: 'browser-review-reject-20260913'});
  await wait(async () => (await page.locator('.tree-proposal').count()) === 1, 'second proposal listed');
  await page.locator('.tree-proposal').first().click();
  await wait(async () => (await page.locator('#review-sections').innerText()).includes('+ relation: relates ticket:3'), 'second proposal loaded');
  const noteLines = await page.locator('#review-sections .line').allInnerTexts();
  assert.ok(noteLines.includes('+ relation: relates ticket:3'), 'relation change is its own line');
  assert.ok(noteLines.includes('+ state: "resolved"'));
  await page.setViewportSize({width: 390, height: 844});
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'no horizontal overflow on narrow screens');
  await page.screenshot({path: join(process.env.TEMP || '/tmp', 'review-mobile.png'), fullPage: true});
  await page.locator('#review-reject').click();
  await wait(async () => (await page.locator('#notice').innerText()).includes('理由'), 'reason required');
  await page.locator('#review-note').fill('Keep the question open.');
  await page.locator('#review-reject').click();
  await wait(async () => (await page.locator('#review-check').innerText()).includes('已拒绝'), 'rejected outcome');
  const rejected = await call('get_proposal', {proposal_id: second.proposal_id});
  assert.equal(rejected.status, 'rejected');
  assert.equal(rejected.result.reason, 'Keep the question open.');
  if (await page.locator('#notice').isVisible()) await page.locator('#notice').click();
  const note = await (await page.request.get(base + '/api/graph')).json();
  assert.equal(note.notes.find(n => n.id === 'n-save-question').state, 'open');

  // Mixed work and its provisional premise share the existing overlay and review form.
  const b3 = (await call('get_ticket', {ticket_id: 3})).basis;
  const third = await call('propose_changes', {operations: [
    {op: 'create', temp_id: 'new:work', fields: {title: 'Mixed work', group_name: 'Mixed fixture'}},
    {op: 'note_create', temp_id: 'new:premise', path: 'notes/n-mixed.md', fields: {
      title: 'Mixed premise', kind: 'assumption', state: 'proposed', body: 'An unverified fixture claim.',
      relations: [{type: 'assumes', target: 'ticket:new:work'}]}}
  ], basis: b3, base_revision: b3.revision, notes_revision: b3.notes_revision,
    summary: 'Browser mixed fixture', rationale: 'Review work and its premise together.',
    origin: 'free', request_id: 'browser-review-mixed-20260917'});
  await wait(async () => (await page.locator('.tree-proposal').count()) === 1, 'mixed proposal listed');
  assert.ok((await page.locator('#proposals-tree').innerText()).includes('Mixed fixture'));
  assert.ok((await page.locator('#proposals-tree').innerText()).includes('工单与笔记'));
  await page.locator('.tree-proposal').first().click();
  await wait(async () => (await page.locator('#review-sections').innerText()).includes('ticket:new:work'), 'mixed relation diff');
  await wait(() => page.locator('#dag .node.changed').count().then(n => n >= 2), 'mixed ticket and note overlay');
  assert.equal(await page.locator('#dag .edge[data-source="n:n-mixed"][data-target="t:new:work"]').count(), 1, 'mixed premise edge targets the preview ticket');
  for (const [label, width, height] of [['desktop', 1365, 960], ['mobile', 390, 844]]) {
    await page.setViewportSize({width, height});
    await page.locator('#fit').click();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'mixed review fits ' + label);
    await page.screenshot({path: join(process.env.TEMP || '/tmp', `review-mixed-${label}.png`), fullPage: true});
  }
  const mixedBody = page.locator('#review-edits textarea[data-field=body]');
  if (!(await mixedBody.isVisible())) await page.locator('#review-edit summary').click();
  await mixedBody.fill('A narrower fixture claim.');
  await page.locator('#review-apply').click();
  await wait(async () => (await page.locator('#review-check').innerText()).includes('已应用'), 'mixed applied outcome');
  const mixed = await call('get_proposal', {proposal_id: third.proposal_id});
  assert.equal(mixed.status, 'applied');
  const finalGraph = await (await page.request.get(base + '/api/graph')).json();
  const newNote = finalGraph.notes.find(n => n.id === 'n-mixed');
  const assigned = mixed.result.created['new:work'];
  assert.ok(finalGraph.tickets.some(t => t.id === assigned && t.title === 'Mixed work'));
  assert.deepEqual(newNote.relations, [{type: 'assumes', target: `ticket:${assigned}`}]);
  assert.equal(newNote.body, 'A narrower fixture claim.');
  assert.equal(newNote.state, 'proposed');
  assert.deepEqual(errors, []);
  console.log('Proposal browser checks passed: SSE count, grouped list, mixed overlay, canonical relations, line diffs, escaping, edit-and-apply, reason-gated reject, narrow width.');
} finally {
  await browser.close();
}
