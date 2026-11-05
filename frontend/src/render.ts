function escapeHtml(value: unknown): string {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char] || char);
}

function safeHref(value: string): string | null {
  const href = value.replace(/&amp;/g, '&').replace(/&quot;/g, '"');
  if (/^#[a-zA-Z0-9_-]+$/.test(href) || /^[a-zA-Z0-9_./-]+\.md(?:#[a-zA-Z0-9_-]+)?$/.test(href)) return href;
  return null;
}

function inlineMarkdown(value: string): string {
  return value.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label: string, href: string) => {
    const safe = safeHref(href);
    return safe
      ? '<a href="' + escapeHtml(safe) + '" data-doc-link="' + escapeHtml(safe) + '">' + label + '</a>'
      : '[' + label + '](' + escapeHtml(href) + ')';
  }).replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>').replace(/\x60([^\x60]+)\x60/g, '<code>$1</code>');
}

export function renderMarkdown(value: string): string {
  const anchors: string[] = [];
  const codeBlocks: string[] = [];
  const source = String(value ?? '').replace(/<a id="([a-zA-Z0-9_-]+)"><\/a>/g, (_, id: string) => {
    anchors.push(id);
    return '@@DOC_ANCHOR_' + (anchors.length - 1) + '@@';
  });
  let text = escapeHtml(source);
  const codeFence = new RegExp('^\x60\x60\x60(\\w*)\\n([\\s\\S]*?)\x60\x60\x60$', 'gm');
  text = text.replace(codeFence, (_, _lang: string, body: string) => {
    codeBlocks.push('<pre><code>' + body + '</code></pre>');
    return '@@DOC_CODE_' + (codeBlocks.length - 1) + '@@';
  });
  text = text.replace(/^### (.*)$/gm, '<h3>$1</h3>').replace(/^## (.*)$/gm, '<h2>$1</h2>').replace(/^# (.*)$/gm, '<h1>$1</h1>');
  text = text.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label: string, href: string) => {
    const safe = safeHref(href);
    return safe
      ? '<a href="' + escapeHtml(safe) + '" data-doc-link="' + escapeHtml(safe) + '">' + label + '</a>'
      : '[' + label + '](' + escapeHtml(href) + ')';
  });
  text = text.replace(/(^|\n)\|([^\n]+)\n\|[- :|]+\|\n((?:\|[^\n]+\|\n?)*)/g, (_, prefix: string, header: string, body: string) => {
    const cells = (row: string) => row.trim().replace(/^\||\|$/g, '').split('|').map((cell) => cell.trim());
    const headings = cells(header).map((cell) => '<th>' + inlineMarkdown(cell) + '</th>').join('');
    const rows = body.trim().split('\n').filter(Boolean).map((row) => '<tr>' + cells(row).map((cell) => '<td>' + inlineMarkdown(cell) + '</td>').join('') + '</tr>').join('');
    return prefix + '<table><thead><tr>' + headings + '</tr></thead><tbody>' + rows + '</tbody></table>\n';
  });
  text = text.replace(/(^|\n)((?:[-*] [^\n]+\n?)+)/g, (_, prefix: string, lines: string) => prefix + '<ul>' + lines.trim().split('\n').map((line) => '<li>' + inlineMarkdown(line.slice(2)) + '</li>').join('') + '</ul>');
  text = text.replace(/(^|\n)((?:\d+\. [^\n]+\n?)+)/g, (_, prefix: string, lines: string) => prefix + '<ol>' + lines.trim().split('\n').map((line) => '<li>' + inlineMarkdown(line.replace(/^\d+\. /, '')) + '</li>').join('') + '</ol>');
  text = text.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>').replace(/\x60([^\x60]+)\x60/g, '<code>$1</code>');
  text = text.replace(/@@DOC_ANCHOR_(\d+)@@/g, (_, index: string) => '<a id="' + anchors[Number(index)] + '"></a>');
  text = text.replace(/@@DOC_CODE_(\d+)@@/g, (_, index: string) => codeBlocks[Number(index)]);
  return text.split(/\n{2,}/).map((block) => /^<(h[1-6]|pre|ul|ol|table|a|p)\b/.test(block) ? block : '<p>' + block.replace(/\n/g, '<br>') + '</p>').join('');
}

export function renderDiff(value: string): string {
  const rows = escapeHtml(value).split('\n').map((line) => {
    const kind = line.startsWith('+') ? 'add' : line.startsWith('-') ? 'del' : '';
    return '<div class="' + kind + '">' + (line || ' ') + '</div>';
  });
  return '<div class="diff">' + rows.join('') + '</div>';
}

export function streamingDiff(base: string, draft: string): string {
  const before = base.split('\n');
  const after = draft.split('\n');
  let prefix = 0;
  while (prefix < before.length && prefix < after.length && before[prefix] === after[prefix]) prefix += 1;
  let beforeEnd = before.length - 1;
  let afterEnd = after.length - 1;
  while (beforeEnd >= prefix && afterEnd >= prefix && before[beforeEnd] === after[afterEnd]) {
    beforeEnd -= 1;
    afterEnd -= 1;
  }
  const lines = ['--- disk', '+++ draft'];
  lines.push(...before.slice(0, prefix).map((line) => ' ' + line));
  lines.push(...before.slice(prefix, beforeEnd + 1).map((line) => '-' + line));
  lines.push(...after.slice(prefix, afterEnd + 1).map((line) => '+' + line));
  lines.push(...before.slice(beforeEnd + 1).map((line) => ' ' + line));
  return lines.join('\n');
}

export function escapeText(value: unknown): string {
  return escapeHtml(value);
}
