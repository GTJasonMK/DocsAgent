<script setup lang="ts">
import type { StatusKind, ViewMode } from '../types';

const props = defineProps<{
  tabs: string[];
  current: string | null;
  view: ViewMode;
  contentHtml: string;
  follow: boolean;
  sourceAvailable: boolean;
  sourceTitle: string;
  proposalCount: number;
  status: string;
  statusState: StatusKind | '';
}>();
const emit = defineEmits<{
  openTab: [path: string];
  view: [view: ViewMode];
  source: [];
  graph: [];
  proposals: [];
  follow: [];
  contentClick: [event: MouseEvent];
}>();

const views = [
  { id: 'preview', label: '预览' },
  { id: 'source', label: '源码' },
  { id: 'split', label: '并排' },
  { id: 'diff', label: 'Diff' },
] as const;
</script>

<template>
  <section class="editor">
    <div id="tabs" class="tabs">
      <button
        v-for="path in props.tabs"
        :key="path"
        type="button"
        class="tab"
        :class="{ active: path === props.current }"
        :aria-label="'打开 ' + path"
        @click="emit('openTab', path)"
      >{{ path.split('/').at(-1) }}</button>
    </div>
    <div class="editor-toolbar">
      <span id="currentPath" class="path">{{ props.current || '未选择文件' }}</span>
      <div class="editor-actions">
        <button type="button" :class="{ active: props.follow }" :aria-pressed="props.follow" title="让视图跟随 Agent 当前文件" @click="emit('follow')">{{ props.follow ? '跟随中' : '跟随' }}</button>
        <button type="button" :disabled="!props.sourceAvailable" :title="props.sourceTitle" @click="emit('source')">源码</button>
        <button type="button" title="查看静态实现链路" @click="emit('graph')">链路</button>
        <button v-if="props.proposalCount" type="button" @click="emit('proposals')">待审提案 ({{ props.proposalCount }})</button>
      </div>
      <div class="view-switch" role="tablist" aria-label="文档视图">
        <button
          v-for="item in views"
          :key="item.id"
          type="button"
          role="tab"
          aria-controls="content"
          :aria-selected="props.view === item.id"
          :class="{ active: props.view === item.id }"
          @click="emit('view', item.id)"
        >{{ item.label }}</button>
      </div>
    </div>
    <div id="content" class="content markdown" role="tabpanel" tabindex="0" v-html="props.contentHtml" @click="emit('contentClick', $event)"></div>
    <div id="status" class="statusbar" role="status" aria-live="polite" :data-state="props.statusState">{{ props.status }}</div>
  </section>
</template>
