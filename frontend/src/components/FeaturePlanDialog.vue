<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { request } from '../api';
import { renderDiff } from '../render';
import type { AgentSettings, CommitHistory, CommitRangeDiff, FeaturePlanRequest, GitStatus, TreeResponse } from '../types';

const props = defineProps<{
  open: boolean;
  git: GitStatus | null;
  settings: AgentSettings | null;
  tree: TreeResponse | null;
  busy: boolean;
}>();
const emit = defineEmits<{
  close: [];
  settings: [];
  submit: [request: FeaturePlanRequest, goal: string];
}>();

const history = ref<CommitHistory | null>(null);
const dialogElement = ref<HTMLDialogElement | null>(null);
const fromCommit = ref('');
const goal = ref('');
const featureId = ref('');
const action = ref<'create' | 'modify'>('create');
const diff = ref<CommitRangeDiff | null>(null);
const diffHtml = ref('');
const loading = ref(false);
const loadingDiff = ref(false);
const error = ref('');

const featureDirectory = computed(() => `docs/features/${featureId.value.trim()}`);
const packageExists = computed(() => Boolean(props.tree?.feature_ids?.includes(featureId.value.trim())
  || props.tree?.documents.some((item) => item.path.startsWith(featureDirectory.value + '/'))));
const allowedWorktreePaths = computed(() => new Set([
  `${featureDirectory.value}/requirements.md`,
  `${featureDirectory.value}/architecture.md`,
  `${featureDirectory.value}/plan.md`,
]));
const unrelatedChanges = computed(() => (props.git?.worktree_entries || [])
  .filter((entry) => !allowedWorktreePaths.value.has(entry.path))
  .map((entry) => entry.path));
const hasRange = computed(() => Boolean(fromCommit.value));
const hasHistoryForRange = computed(() => (history.value?.items.length || 0) > 1);
const permissionReady = computed(() => Boolean(props.settings?.configured && props.settings.allow_source_upload));
const packageChoiceValid = computed(() => action.value === 'create' ? !packageExists.value : packageExists.value);
const canSubmit = computed(() => Boolean(
  featureId.value.trim()
  && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(featureId.value.trim())
  && goal.value.trim()
  && permissionReady.value
  && history.value?.head_commit
  && packageChoiceValid.value
  && unrelatedChanges.value.length === 0
  && !props.busy
  && (!hasHistoryForRange.value || hasRange.value)
  && (!hasRange.value || (diff.value && !diff.value.truncated && diff.value.to_commit === history.value?.head_commit))
  && !loading.value
));

async function loadHistory() {
  if (!props.open) return;
  loading.value = true;
  error.value = '';
  history.value = null;
  diff.value = null;
  fromCommit.value = '';
  try {
    history.value = await request<CommitHistory>('/api/git/commits');
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loading.value = false;
  }
}

async function loadDiff() {
  diff.value = null;
  diffHtml.value = '';
  if (!fromCommit.value || !history.value?.head_commit) return;
  loadingDiff.value = true;
  error.value = '';
  try {
    const query = new URLSearchParams({ from: fromCommit.value, to: history.value.head_commit });
    const result = await request<CommitRangeDiff>('/api/git/compare?' + query.toString());
    diff.value = result;
    diffHtml.value = renderDiff(result.diff || '区间内没有可读取的安全文本变更。');
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause);
  } finally {
    loadingDiff.value = false;
  }
}

function submit() {
  if (!canSubmit.value || !history.value?.head_commit) return;
  emit('submit', {
    feature_id: featureId.value.trim(),
    action: action.value,
    ...(fromCommit.value ? { from_commit: fromCommit.value, to_commit: history.value.head_commit } : {}),
  }, goal.value.trim());
}

watch(() => props.open, (open) => { if (open) void loadHistory(); });
watch(fromCommit, () => { void loadDiff(); });
onMounted(() => {
  if (!props.open) return;
  dialogElement.value?.showModal();
  void loadHistory();
});
</script>

<template>
  <dialog v-if="props.open" ref="dialogElement" class="feature-plan-dialog" aria-labelledby="featurePlanTitle" @cancel.prevent="emit('close')">
    <form class="feature-plan-shell" @submit.prevent="submit">
      <header class="feature-plan-header">
        <div><p class="eyebrow">FEATURE PLANNING</p><h2 id="featurePlanTitle">制定功能计划</h2></div>
        <button type="button" class="icon-close" aria-label="关闭功能计划" @click="emit('close')">×</button>
      </header>
      <div class="feature-plan-body">
        <div v-if="!permissionReady" class="workflow-notice" data-state="warning">
          <span>功能计划需要已配置的模型和“允许 Agent 读取项目内容”。</span>
          <button type="button" @click="emit('settings')">打开 Agent 设置</button>
        </div>
        <div v-if="props.busy" class="workflow-notice" data-state="warning">请等待当前 Agent 或同步任务完成后再开始功能计划。</div>
        <div v-if="!props.git?.is_repo || !history?.head_commit" class="workflow-notice" data-state="warning">项目需要至少一个 Git 基线提交；DocsAgent 不会自动初始化或提交。</div>
        <label>功能 ID<input v-model="featureId" maxlength="64" autocomplete="off" placeholder="例如 add-search" spellcheck="false"></label>
        <label>计划目标<textarea v-model="goal" rows="3" maxlength="4000" placeholder="描述要新增或修改的功能，以及希望解决的问题" /></label>
        <label>功能包操作<select v-model="action"><option value="create">创建新功能包</option><option value="modify">明确修改现有功能包</option></select></label>
        <p v-if="featureId && action === 'create' && packageExists" class="workflow-error">该功能包已存在；请选择修改，或换一个 ID。</p>
        <p v-if="featureId && action === 'modify' && !packageExists" class="workflow-error">该功能包不存在；请选择创建。</p>

        <section class="commit-range-box" aria-labelledby="commitRangeTitle">
          <div class="range-heading"><div><strong id="commitRangeTitle">代码变化依据</strong><p>区间终点固定为当前 HEAD；计划基线也记录为当前 HEAD。</p></div></div>
          <div v-if="history?.head_commit" class="head-commit"><span>当前 HEAD</span><code>{{ history.head_commit }}</code></div>
          <label v-if="hasHistoryForRange">区间起点
            <select v-model="fromCommit" aria-label="区间起点">
              <option value="">选择较早的提交</option>
              <option v-for="item in history?.items.filter((item) => item.commit !== history?.head_commit) || []" :key="item.commit" :value="item.commit">{{ item.date }} · {{ item.subject }} · {{ item.commit.slice(0, 8) }}</option>
            </select>
          </label>
          <p v-else-if="history?.head_commit" class="overview-muted">这是初始基线；本轮可以直接基于当前仓库制定计划。</p>
          <div v-if="fromCommit" class="range-preview">
            <div class="range-files"><strong>{{ diff?.files.length || 0 }} 个安全文本文件</strong><span v-if="loadingDiff">正在读取 Diff…</span><span v-else-if="diff?.truncated" class="workflow-error">Diff 太长，请选择更窄的区间。</span></div>
            <ul v-if="diff?.files.length"><li v-for="path in diff.files" :key="path"><code>{{ path }}</code></li></ul>
            <div v-if="diffHtml" class="range-diff" v-html="diffHtml" />
          </div>
        </section>

        <div v-if="unrelatedChanges.length" class="workflow-notice" data-state="error">
          <strong>先提交或处理功能包外的工作区变更</strong>
          <ul><li v-for="path in unrelatedChanges.slice(0, 12)" :key="path"><code>{{ path }}</code></li></ul>
        </div>
        <p v-if="error" class="workflow-error" role="alert">{{ error }}</p>
      </div>
      <footer class="feature-plan-actions">
        <span class="overview-muted">生成后审核三份文档，再单独提交计划；实现和测试由外部工具完成。</span>
        <button type="button" @click="emit('close')">取消</button>
        <button type="submit" class="primary" :disabled="!canSubmit">生成计划草稿</button>
      </footer>
    </form>
  </dialog>
</template>
