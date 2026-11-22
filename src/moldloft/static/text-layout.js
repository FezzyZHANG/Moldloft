// English words stay together. CJK text can wrap between characters.
export function wrapText(text, width, measure, maxLines = 2) {
  if (maxLines < 1 || width <= 0) return [];
  const tokens = String(text).match(/[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]|[^\s\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]+|\s+/gu) || [];
  const lines = [];
  let line = '';
  function shorten(value) {
    const chars = [...value.trimEnd()];
    while (chars.length && measure(chars.join('') + '…') > width) chars.pop();
    return chars.join('').trimEnd() + '…';
  }
  for (const token of tokens) {
    if (!token.trim()) {
      if (line) line += ' ';
      continue;
    }
    if (measure(line + token) <= width) {
      line += token;
      continue;
    }
    if (lines.length === maxLines - 1) return [...lines, shorten(line || token)];
    if (line) {
      lines.push(line.trimEnd());
      line = '';
    }
    if (measure(token) <= width) line = token;
    else if (lines.length === maxLines - 1) return [...lines, shorten(token)];
    else lines.push(shorten(token));
  }
  if (line) lines.push(line.trimEnd());
  return lines;
}
