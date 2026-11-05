import { describe, expect, it } from 'vitest';
import { renderDiff, renderMarkdown, streamingDiff } from '../src/render';

describe('renderMarkdown', () => {
  it('renders headings, lists, code, tables, and project links', () => {
    const codeFence = String.fromCharCode(96).repeat(3);
    const codeMark = String.fromCharCode(96);
    const html = renderMarkdown([
      '# Guide',
      '',
      '- **First** item',
      '- ' + codeMark + 'second' + codeMark + ' item',
      '',
      '| Name | Value |',
      '| --- | --- |',
      '| x | 1 |',
      '',
      '[Next](next.md#setup)',
      '',
      codeFence + 'ts',
      'const value = 1;',
      codeFence,
    ].join('\n'));

    expect(html).toContain('<h1>Guide</h1>');
    expect(html).toContain('<strong>First</strong>');
    expect(html).toContain('<code>second</code>');
    expect(html).toContain('<table>');
    expect(html).toContain('data-doc-link="next.md#setup"');
    expect(html).toContain('<pre><code>const value = 1;\n</code></pre>');
  });

  it('escapes HTML and rejects external link schemes', () => {
    const html = renderMarkdown('<img src=x onerror=alert(1)>\n\n[unsafe](javascript:alert(1))');
    expect(html).toContain('&lt;img src=x onerror=alert(1)&gt;');
    expect(html).not.toContain('<img');
    expect(html).not.toContain('href="javascript:');
  });
});

describe('diff rendering', () => {
  it('classifies additions and deletions without injecting markup', () => {
    const html = renderDiff('-old\n+<script>alert(1)</script>');
    expect(html).toContain('class="del"');
    expect(html).toContain('class="add"');
    expect(html).toContain('&lt;script&gt;');
  });

  it('builds a compact draft diff around the changed lines', () => {
    expect(streamingDiff('a\nb\nc', 'a\nB\nc')).toBe('--- disk\n+++ draft\n a\n-b\n+B\n c');
  });
});
