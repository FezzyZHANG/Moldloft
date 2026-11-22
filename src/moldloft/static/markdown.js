// Markdown and reference captions are untrusted text.
function inline(parent, text, navigate) {
  const tokens = /`([^`\n]+)`|\*\*([^*\n]+)\*\*|~~([^~\n]+)~~|\*([^*\n]+)\*|(?<![a-zA-Z0-9_#-])(#\d+|n-[a-z0-9][a-z0-9-]{0,62})(?![a-zA-Z0-9_-])/gu;
  let cursor = 0;
  for (const match of text.matchAll(tokens)) {
    parent.append(text.slice(cursor, match.index));
    const [, code, bold, strike, italic, reference] = match;
    const node = document.createElement(
      reference ? 'button' : code ? 'code' : bold ? 'strong' : strike ? 'del' : 'em',
    );
    if (reference) {
      node.type = 'button';
      node.className = 'text-link';
      node.textContent = reference;
      node.addEventListener('click', () => navigate(reference));
    } else if (code) node.textContent = code;
    else inline(node, bold || strike || italic, navigate);
    parent.append(node);
    cursor = match.index + match[0].length;
  }
  parent.append(text.slice(cursor));
}

export function markdown(text, navigate) {
  const preview = document.createElement('div');
  preview.className = 'markdown';
  const lines = String(text ?? '').split(/\r?\n/);
  let paragraph = [];
  let list = null;
  let listTag = null;
  let quote = [];
  let fenced = null;
  let fencedLang = '';
  function flushParagraph() {
    if (!paragraph.length) return;
    const node = document.createElement('p');
    inline(node, paragraph.map(part => part.trim()).join(' '), navigate);
    preview.append(node);
    paragraph = [];
  }
  function flushQuote() {
    if (!quote.length) return;
    const node = document.createElement('blockquote');
    inline(node, quote.map(part => part.trim()).join(' '), navigate);
    preview.append(node);
    quote = [];
  }
  function flushBlocks() {
    flushParagraph();
    flushQuote();
    list = null;
    listTag = null;
  }
  function codeBlock() {
    const pre = document.createElement('pre');
    pre.className = 'code-block';
    if (fencedLang) {
      const lang = document.createElement('span');
      lang.className = 'code-lang';
      lang.textContent = fencedLang;
      pre.append(lang);
    }
    const code = document.createElement('code');
    code.textContent = fenced.join('\n');
    pre.append(code);
    preview.append(pre);
    fenced = null;
    fencedLang = '';
  }
  for (const line of lines) {
    if (fenced) {
      if (/^\s*```\s*$/.test(line)) codeBlock();
      else fenced.push(line);
      continue;
    }
    const fenceOpen = /^\s*```(\S*)\s*$/.exec(line);
    const heading = /^\s*(#{1,6})\s+(.+)$/.exec(line);
    const task = /^\s*[-*+]\s+\[([ xX])\]\s+(.+)$/.exec(line);
    const bullet = !task && /^\s*[-*+]\s+(.+)$/.exec(line);
    const ordered = /^\s*\d+\.\s+(.+)$/.exec(line);
    const blockquote = /^\s*>\s?(.*)$/.exec(line);
    const rule = /^\s*(?:---+|\*\*\*+)\s*$/.test(line);

    if (fenceOpen) {
      flushBlocks();
      fenced = [];
      fencedLang = fenceOpen[1];
    } else if (rule) {
      flushBlocks();
      preview.append(document.createElement('hr'));
    } else if (heading) {
      flushBlocks();
      const node = document.createElement(`h${heading[1].length}`);
      inline(node, heading[2], navigate);
      preview.append(node);
    } else if (blockquote) {
      flushParagraph();
      list = null;
      listTag = null;
      quote.push(blockquote[1]);
    } else if (task || bullet || ordered) {
      flushParagraph();
      flushQuote();
      const wanted = ordered ? 'ol' : 'ul';
      if (!list || listTag !== wanted) {
        list = document.createElement(wanted);
        listTag = wanted;
        preview.append(list);
      }
      const item = document.createElement('li');
      if (task) {
        const done = task[1].toLowerCase() === 'x';
        item.className = done ? 'md-task done' : 'md-task';
        const marker = document.createElement('span');
        marker.className = 'task-check';
        marker.textContent = done ? '[✓]' : '[ ]';
        item.append(marker, ' ');
        inline(item, task[2], navigate);
      } else {
        inline(item, (bullet || ordered)[1], navigate);
      }
      list.append(item);
    } else if (line.trim()) {
      flushQuote();
      list = null;
      listTag = null;
      paragraph.push(line);
    } else {
      flushBlocks();
    }
  }
  if (fenced) codeBlock();
  flushBlocks();
  return preview;
}
