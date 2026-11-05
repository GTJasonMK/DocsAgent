<script setup lang="ts">
import { computed, ref } from 'vue';
import TreeBranch from './TreeBranch.vue';
import type { Binding, TreeItem, TreeResponse } from '../types';

interface Branch {
  name: string;
  children: Branch[];
  files: TreeItem[];
}

interface Selection {
  item: TreeItem;
  source: boolean;
}

type ExplorerTab = 'documents' | 'sources';

const props = defineProps<{
  tree: TreeResponse | null;
  drafts: Record<string, string>;
}>();
const emit = defineEmits<{ select: [selection: Selection] }>();
const activeTab = ref<ExplorerTab>('documents');
const tabs = [
  { id: 'documents', label: '文档目录' },
  { id: 'sources', label: '源码' },
] as const;

function onTabKeydown(event: KeyboardEvent) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault();

  const currentIndex = tabs.findIndex((tab) => tab.id === activeTab.value);
  const nextIndex = event.key === 'Home'
    ? 0
    : event.key === 'End'
      ? tabs.length - 1
      : (currentIndex + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
  activeTab.value = tabs[nextIndex].id;
  (event.currentTarget as HTMLElement)
    .querySelector<HTMLButtonElement>(`[data-tab-id="${activeTab.value}"]`)
    ?.focus();
}

function documentPathForSource(sourcePath: string, binding: Binding): string | null {
  const sourceRoot = binding.source_root.replace(/\/$/, '');
  if (sourceRoot && sourceRoot !== '.' && sourcePath !== sourceRoot && !sourcePath.startsWith(sourceRoot + '/')) return null;
  const docsRoot = binding.docs_root === '.' ? '' : binding.docs_root.replace(/\/$/, '');
  return [docsRoot, sourcePath + '.md'].filter(Boolean).join('/');
}

function makeBranch(items: TreeItem[]): Branch {
  const root: Branch = { name: '', children: [], files: [] };
  for (const item of items) {
    const parts = item.path.split('/');
    let current = root;
    for (const name of parts.slice(0, -1)) {
      let child = current.children.find((candidate) => candidate.name === name);
      if (!child) {
        child = { name, children: [], files: [] };
        current.children.push(child);
      }
      current = child;
    }
    current.files.push({ ...item, status: item.status || 'synced' });
  }
  const sort = (branch: Branch) => {
    branch.children.sort((a, b) => a.name.localeCompare(b.name));
    branch.files.sort((a, b) => a.path.localeCompare(b.path));
    branch.children.forEach(sort);
  };
  sort(root);
  return root;
}

const documents = computed(() => {
  if (!props.tree) return [];
  const draftPaths = new Set(Object.keys(props.drafts));
  return props.tree.documents.map((item) => draftPaths.has(item.path) ? { ...item, status: props.drafts[item.path] } : item);
});
const documentPaths = computed(() => new Set(documents.value.map((item) => item.path)));
const draftOnly = computed(() => Object.entries(props.drafts)
  .filter(([path]) => !documentPaths.value.has(path))
  .map(([path, status]) => ({ path, status, kind: 'document' })));
const sources = computed(() => {
  const binding = props.tree?.binding;
  return (props.tree?.sources || []).map((item) => {
    const path = binding ? documentPathForSource(item.path, binding) : null;
    const status = path ? props.drafts[path] : undefined;
    return status ? { ...item, status } : item;
  });
});
const documentBranch = computed(() => makeBranch([...documents.value, ...draftOnly.value]));
const sourceBranch = computed(() => makeBranch(sources.value));
const draftCount = computed(() => Object.keys(props.drafts).length);
const readyCount = computed(() => Object.values(props.drafts).filter((value) => value === 'draft').length);
</script>

<template>
  <aside id="explorer" class="sidebar explorer">
    <div class="explorer-tabs" role="tablist" aria-label="项目文件类型" @keydown="onTabKeydown">
      <button
        v-for="tab in tabs"
        :id="'explorer-tab-' + tab.id"
        :key="tab.id"
        :data-tab-id="tab.id"
        type="button"
        role="tab"
        :aria-selected="activeTab === tab.id"
        :aria-controls="'explorer-panel-' + tab.id"
        :tabindex="activeTab === tab.id ? 0 : -1"
        @click="activeTab = tab.id"
      >{{ tab.label }}</button>
    </div>
    <div
      id="explorer-panel-documents"
      class="tree explorer-tree"
      role="tabpanel"
      aria-labelledby="explorer-tab-documents"
      tabindex="0"
      v-show="activeTab === 'documents'"
    >
      <TreeBranch :folder="documentBranch" @select="emit('select', $event)" />
      <div v-if="draftCount" class="draft-summary">
        <strong>本次同步草稿</strong>
        <span>{{ readyCount }}/{{ draftCount }} 个文件已生成</span>
        <small>尚未写入磁盘</small>
      </div>
    </div>
    <div
      id="explorer-panel-sources"
      class="tree explorer-tree"
      role="tabpanel"
      aria-labelledby="explorer-tab-sources"
      tabindex="0"
      v-show="activeTab === 'sources'"
    >
      <TreeBranch :folder="sourceBranch" source @select="emit('select', $event)" />
    </div>
  </aside>
</template>
