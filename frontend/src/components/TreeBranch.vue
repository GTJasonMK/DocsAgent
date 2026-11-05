<script setup lang="ts">
import type { TreeItem } from '../types';

interface TreeFolder {
  name: string;
  children: TreeFolder[];
  files: TreeItem[];
}

interface Selection {
  item: TreeItem;
  source: boolean;
}

defineOptions({ name: 'TreeBranch' });
const props = defineProps<{ folder: TreeFolder; source?: boolean }>();
const emit = defineEmits<{ select: [selection: Selection] }>();

const labels: Record<string, string> = {
  missing: '待生成', orphan: '孤立', manual: '手写', draft: '草稿',
  generating: '生成中', error: '校验失败',
};

function select(item: TreeItem) {
  emit('select', { item, source: Boolean(props.source) });
}
</script>

<template>
  <div>
    <details v-for="child in props.folder.children" :key="child.name" open>
      <summary>{{ child.name }}</summary>
      <div class="tree-children">
        <TreeBranch :folder="child" :source="props.source" @select="emit('select', $event)" />
      </div>
    </details>
    <button
      v-for="item in props.folder.files"
      :key="item.path"
      type="button"
      :class="[props.source ? 'source' : '', 'status-' + (item.status || 'synced')]"
      @click="select(item)"
    >
      <template v-if="!props.source">▱ </template>{{ item.path.split('/').at(-1) }}
      <span class="tree-status">{{ labels[item.status || ''] || '' }}</span>
    </button>
  </div>
</template>
