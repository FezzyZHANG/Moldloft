// Markdown and reference captions are untrusted text.
function inline(parent, text, navigate) {
  const tokens = /`([^`\n]+)`|\*\*([^*\n]+)\*\*|\*([^*\n]+)\*|(?<![a-zA-Z0-9_#-])(#\d+|n-[a-z0-9][a-z0-9-]{0,62})(?![a-zA-Z0-9_-])/gu;
  let cursor = 0;
  for (const match of text.matchAll(tokens)) {
    parent.append(text.slice(cursor, match.index));
    const [, code, bold, italic, reference] = match;
    const node = document.createElement(reference ? 'button' : code ? 'code' : bold ? 'strong' : 'em');
    if (reference) {
      node.type = 'button';
      node.className = 'text-link';
      node.textContent = reference;
      node.addEventListener('click', () => navigate(reference));
    } else if (code) node.textContent = code;
    else inline(node, bold || italic, navigate);
    parent.append(node);
    cursor = match.index + match[0].length;
  }
  parent.append(text.slice(cursor));
}

export function markdown(text, navigate) {
  const preview = document.createElement('div');
  preview.className = 'markdown';
  const lines = text.split(/\r?\n/);
  let paragraph = [];
  let list = null;
  let fenced = null;
  function flush() {
    if (!paragraph.length) return;
    const node = document.createElement('p');
    inline(node, paragraph.join('\n'), navigate);
    preview.append(node);
    paragraph = [];
  }
  function codeBlock() {
    const pre = document.createElement('pre');
    const code = document.createElement('code');
    code.textContent = fenced.join('\n');
    pre.append(code);
    preview.append(pre);
    fenced = null;
  }
  for (const line of lines) {
    if (fenced) {
      if (/^\s*```\s*$/.test(line)) codeBlock();
      else fenced.push(line);
      continue;
    }
    const heading = /^\s*(#{1,6})\s+(.+)$/.exec(line);
    const bullet = /^\s*[-*+]\s+(.+)$/.exec(line);
    if (!bullet) list = null;
    if (/^\s*```/.test(line)) {
      flush();
      fenced = [];
    } else if (heading) {
      flush();
      const node = document.createElement(`h${heading[1].length}`);
      inline(node, heading[2], navigate);
      preview.append(node);
    } else if (bullet) {
      flush();
      if (!list) {
        list = document.createElement('ul');
        preview.append(list);
      }
      const item = document.createElement('li');
      inline(item, bullet[1], navigate);
      list.append(item);
    } else if (line.trim()) paragraph.push(line);
    else flush();
  }
  if (fenced) codeBlock();
  flush();
  return preview;
}
