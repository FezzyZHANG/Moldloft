import assert from 'node:assert/strict';
import test from 'node:test';
import {markdown} from '../src/moldloft/static/markdown.js';

class MockNode extends EventTarget {
  constructor(tag) {
    super();
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.className = '';
    this.type = '';
    this._text = '';
  }
  append(...items) {
    for (const item of items) this.children.push(typeof item === 'string' ? {textContent: item} : item);
  }
  set textContent(value) { this._text = String(value); }
  get textContent() {
    return this._text + this.children.map(child => child.textContent || '').join('');
  }
}

test('markdown renders rich formatting, task/ordered lists, quotes, rules, code blocks, and references', t => {
  globalThis.document = {createElement: tag => new MockNode(tag)};
  t.after(() => { delete globalThis.document; });

  const hits = [];
  const root = markdown([
    '# Heading One',
    '',
    '> Quote with `code` and **bold**',
    '',
    '- [x] Done task',
    '- [ ] Pending ~~old~~ #12',
    '',
    '1. First step referencing n-check',
    '2. Second step',
    '',
    '---',
    '',
    '```python',
    'print("ok")',
    '```',
  ].join('\n'), ref => hits.push(ref));

  assert.equal(root.className, 'markdown');
  const tags = root.children.map(node => node.tagName);
  assert.deepEqual(tags, ['H1', 'BLOCKQUOTE', 'UL', 'OL', 'HR', 'PRE']);

  const quote = root.children[1];
  assert.ok(quote.children.some(child => child.tagName === 'CODE' && child.textContent === 'code'));
  assert.ok(quote.children.some(child => child.tagName === 'STRONG' && child.textContent === 'bold'));

  const ul = root.children[2];
  assert.equal(ul.children[0].className, 'md-task done');
  assert.equal(ul.children[0].children[0].textContent, '[✓]');
  assert.equal(ul.children[1].className, 'md-task');
  assert.equal(ul.children[1].children[0].textContent, '[ ]');
  assert.ok(ul.children[1].children.some(child => child.tagName === 'DEL' && child.textContent === 'old'));
  const ticketLink = ul.children[1].children.find(child => child.tagName === 'BUTTON');
  assert.equal(ticketLink.textContent, '#12');
  ticketLink.dispatchEvent(new Event('click'));

  const ol = root.children[3];
  const noteLink = ol.children[0].children.find(child => child.tagName === 'BUTTON');
  assert.equal(noteLink.textContent, 'n-check');
  noteLink.dispatchEvent(new Event('click'));
  assert.deepEqual(hits, ['#12', 'n-check']);

  const pre = root.children[5];
  assert.equal(pre.className, 'code-block');
  assert.equal(pre.children[0].className, 'code-lang');
  assert.equal(pre.children[0].textContent, 'python');
  assert.equal(pre.children[1].textContent, 'print("ok")');
});
