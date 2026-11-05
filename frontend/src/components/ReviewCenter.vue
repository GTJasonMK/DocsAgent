<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { request } from '../api';
import { renderDiff } from '../render';
import type { GitStatus, MarkdownStatus, Proposal, Session } from '../types';

const props = defineProps<{
  syncDrafts: Record<string, string>;
  writeReady: boolean;
  proposals: Proposal[];
  sessions: Session[];
  sessionId: string;
  git: GitStatus | null;
}>();

const emit = defineEmits<{
  close: [];
  refresh: [];
  writeSyncDrafts: [];
  openProposal: [sessionId: string];
  commitMarkdown: [paths: string[], diffDigest: string];
  commitFeature: [featureId: string];
}>();

const markdown = ref<MarkdownStatus | null>(null);
const dialogElement = ref<HTMLDialogElement | null>(null);
const selectedPaths = ref<string[]>([]);
const selectedDiff = ref('');
const selectedDigest = ref('');
const selectedDiffTruncated = ref(false);
const busy = ref(false);
const error = ref('');
const selectedAll = computed(() => Boolean(markdown.value?.entries.length)
  && selectedPaths.value.length === markdown.value?.entries.length);
const currentProposalCount = computed(() => props.proposals.length);
const proposalSessionTitle = (proposal: Proposal) => props.sessions.find((session) => session.id === proposal.session_id)?.title || '当前会话';
const featureIds = computed(() => {
  const ids = new Set<string>();
  for (const entry of props.git?.worktree_entries || []) {
    const match = entry.path.match(/^docs\/features\/([a-z0-9]+(?:-[a-z0-9]+)*)\//);
    if (match) ids.add(match[1]);
  }
  return [...ids].sort();
});
const draftPaths = computed(() => Object.keys(props.syncDrafts));

async function loadMarkdownStatus() {
  error.value = '';
  try {
    markdown.value = await request<MarkdownStatus>('/api/git/markdown/status');
    selectedPaths.value = markdown.value.entries.map((item) => item.path);
    selectedDiff.value = '';
    selectedDigest.value = '';
  } catch (cause) {
    markdown.value = null;
    error.value = cause instanceof Error ? cause.message : String(cause);
  }
}

function togglePath(path: string) {
  selectedPaths.value = selectedPaths.value.includes(path)
    ? selectedPaths.value.filter((item) => item !== path)
    : [...selectedPaths.value, path];
  selectedDiff.value = '';
  selectedDigest.value = '';
  selectedDiffTruncated.value = false;
}

function toggleAll() {
  selectedPaths.value = selectedAll.value ? [] : (markdown.value?.entries || []).map((item) => item.path);
  selectedDiff.value = '';
  selectedDigest.value = '';
  selectedDiffTruncated.value = false;
}

async function previewSelected() {
  if (!selectedPaths.value.length) return;
  busy.value = true;
  error.value = '';
  try {
    const query = new URLSearchParams();
    selectedPaths.value.forEach((path) => query.append('path', path));
    const data = await request<{ paths: string[]; diff: string; digest: string; truncated: boolean }>(`/api/git/markdown/diff?${query.toString()}`);
    selectedDiff.value = renderDiff(data.diff || '所选文件没有 Diff');
    selectedDigest.value = data.digest;
    selectedDiffTruncated.value = data.truncated;
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
    selectedDiff.value = '';
    selectedDigest.value = '';
  } finally {
    busy.value = false;
  }
}

function commitSelected() {
  if (!selectedPaths.value.length || !selectedDigest.value || selectedDiffTruncated.value) return;
  emit('commitMarkdown', [...selectedPaths.value], selectedDigest.value);
}

onMounted(() => {
  dialogElement.value?.showModal();
  void loadMarkdownStatus();
});

defineExpose({ refresh: loadMarkdownStatus });
</script>

<template>
  <dialog ref="dialogElement" class="review-dialog" aria-labelledby="reviewTitle" @cancel.prevent="emit('close')">
    <div class="review-shell">
      <header class="review-header">
        <div><p class="eyebrow">PROJECT CHANGES</p><h2 id="reviewTitle">待审与提交</h2></div>
        <button type="button" class="icon-close" aria-label="关闭待审与提交" @click="emit('close')">×</button>
      </header>
      <div class="review-body">
        <section class="review-section sync-review">
          <div class="review-section-heading"><div><h3>同步草稿</h3><p>{{ draftPaths.length }} 个文件，只有全部校验通过后才能写入</p></div><button type="button" :disabled="!props.writeReady" @click="emit('writeSyncDrafts')">确认写入</button></div>
          <ul v-if="draftPaths.length" class="review-path-list"><li v-for="path in draftPaths" :key="path"><code>{{ path }}</code><span>{{ props.syncDrafts[path] }}</span></li></ul>
          <p v-else class="review-empty">没有待写入的同步草稿。</p>
        </section>

        <section class="review-section proposal-review">
          <div class="review-section-heading"><div><h3>Agent 提案</h3><p>接受提案会写入单个 Markdown 文件；随后仍需 Git 提交</p></div><span class="review-count">{{ currentProposalCount }} 份待审</span></div>
          <ul v-if="props.proposals.length" class="review-path-list"><li v-for="proposal in props.proposals" :key="proposal.proposal_id"><div><code>{{ proposal.path }}</code><span>{{ proposalSessionTitle(proposal) }} · {{ proposal.summary || '待审核' }}</span></div><button type="button" @click="emit('openProposal', proposal.session_id || props.sessionId)">打开审核</button></li></ul>
          <p v-else class="review-empty">没有待审提案。</p>
        </section>

        <section class="review-section markdown-review">
          <div class="review-section-heading"><div><h3>工作区 Markdown</h3><p>只提交选中的安全 Markdown；源码和功能计划不会进入此提交</p></div><button type="button" class="quiet-action" :disabled="!markdown?.entries.length" @click="toggleAll">{{ selectedAll ? '取消全选' : '全选' }}</button></div>
          <p v-if="error" class="review-error" role="alert">{{ error }}</p>
          <p v-if="!props.git?.is_repo" class="review-empty">项目尚未初始化 Git。</p>
          <p v-else-if="markdown && !markdown.entries.length" class="review-empty">没有可提交的 Markdown 变更。</p>
          <ul v-else class="review-select-list">
            <li v-for="entry in markdown?.entries || []" :key="entry.path">
              <label><input type="checkbox" :checked="selectedPaths.includes(entry.path)" @change="togglePath(entry.path)"><code>{{ entry.path }}</code></label>
              <small>{{ entry.code }}</small>
            </li>
          </ul>
          <div class="review-actions">
            <button type="button" :disabled="busy || !selectedPaths.length" @click="previewSelected">预览所选 Diff</button>
            <button type="button" class="primary" :disabled="busy || !selectedDigest || selectedDiffTruncated" @click="commitSelected">提交所选文档</button>
          </div>
          <p v-if="selectedDiffTruncated" class="review-error" role="status">Diff 已截断，缩小文件选择后再提交。</p>
          <div v-if="selectedDiff" class="review-diff" aria-label="所选 Markdown 的 Git Diff" v-html="selectedDiff" />
          <button type="button" class="quiet-action" @click="loadMarkdownStatus">刷新 Markdown 变更</button>
        </section>

        <section v-if="featureIds.length" class="review-section feature-review">
          <div class="review-section-heading"><div><h3>功能计划包</h3><p>独立校验基线和完整的三份计划文档后提交</p></div></div>
          <div class="feature-plan-list"><button v-for="featureId in featureIds" :key="featureId" type="button" @click="emit('commitFeature', featureId)">审核并提交 {{ featureId }}</button></div>
        </section>
      </div>
      <footer class="review-footer"><span>写入工作区不会创建 Git 提交。</span><button type="button" @click="emit('refresh')">刷新项目状态</button></footer>
    </div>
  </dialog>
</template>
