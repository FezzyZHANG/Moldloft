// Keep ordinary English words intact; CJK can wrap between characters. An overlong token
// is shortened visibly rather than looking like two different words on adjacent lines.
export function wrapText(text, width, measure, maxLines = 2) {
  const tokens = String(text).match(/[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}]|[^\s\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}]+|\s+/gu) || [];
  const result = []; let line = '';
  const ellipsis = value => {
    const chars = [...value.trimEnd()];
    while (chars.length && measure(chars.join('') + '…') > width) chars.pop();
    return chars.join('') + '…';
  };
  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i], next = (line + token).trimStart();
    if (measure(next.trimEnd()) <= width) { line = next; continue; }
    if (result.length === maxLines - 1) { result.push(ellipsis(line || token)); return result; }
    if (line.trim()) { result.push(line.trimEnd()); line = ''; i--; }
    else { result.push(ellipsis(token)); if (result.length === maxLines) return result; }
  }
  if (line.trim()) result.push(line.trimEnd());
  return result;
}
