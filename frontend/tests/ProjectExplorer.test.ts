import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import ProjectExplorer from '../src/components/ProjectExplorer.vue';

describe('ProjectExplorer', () => {
  it('shows document drafts and emits document/source selection', async () => {
    const wrapper = mount(ProjectExplorer, {
      props: {
        tree: {
          binding: { source_root: 'src', docs_root: 'docs' },
          documents: [{ path: 'docs/guide.md', status: 'synced' }],
          sources: [{ path: 'src/main.py' }],
        },
        drafts: { 'docs/new.md': 'draft' },
      },
    });

    expect(wrapper.text()).toContain('1/1 个文件已生成');
    expect(wrapper.text()).toContain('new.md');
    expect(wrapper.get('#explorer-panel-documents').isVisible()).toBe(true);
    expect(wrapper.get('#explorer-panel-sources').isVisible()).toBe(false);
    await wrapper.get('button.status-synced').trigger('click');
    expect(wrapper.emitted('select')?.[0]?.[0]).toEqual({ item: { path: 'docs/guide.md', status: 'synced' }, source: false });
    await wrapper.get('[data-tab-id="sources"]').trigger('click');
    expect((wrapper.get('#explorer-panel-documents').element as HTMLElement).style.display).toBe('none');
    expect((wrapper.get('#explorer-panel-sources').element as HTMLElement).style.display).not.toBe('none');
    await wrapper.get('button.source').trigger('click');
    expect(wrapper.emitted('select')?.[1]?.[0]).toEqual({ item: { path: 'src/main.py', status: 'synced' }, source: true });
  });
});
