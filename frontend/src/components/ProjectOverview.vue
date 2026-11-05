<script setup lang="ts">
import { computed } from 'vue';
import type { AgentSettings, GitStatus, MarkdownStatus, PendingWork, TreeResponse } from '../types';

const props = defineProps<{
  tree: TreeResponse | null;
  git: GitStatus | null;
  markdown: MarkdownStatus | null;
  settings: AgentSettings | null;
  pending: PendingWork | null;
  loading: boolean;
}>();

const emit = defineEmits<{
  enter: [];
  ask: [];
  sync: [];
  document: [];
  plan: [];
  review: [];
  binding: [];
  settings: [];
}>();

const missingCount = computed(() => props.tree?.sources.filter((item) => item.status === 'missing').length || 0);
const orphanCount = computed(() => props.tree?.documents.filter((item) => item.status === 'orphan').length || 0);
const worktreeCount = computed(() => props.git?.worktree_entries?.length ?? props.git?.entries.length ?? 0);
const pendingCount = computed(() => (props.pending?.sync_files.length || 0)
  + (props.pending?.proposal_paths.length || 0)
  + (props.markdown?.changed_documents || 0));
</script>

<template>
  <main class="project-overview" aria-labelledby="overviewTitle">
    <header class="overview-header">
      <div class="overview-heading">
        <p class="eyebrow">PROJECT WORKSPACE</p>
        <h1 id="overviewTitle">{{ props.tree?.project_name || '项目概览' }}</h1>
        <p class="overview-lead">先确认项目范围，再选择要处理的工作。</p>
      </div>
      <button type="button" class="primary overview-open" :disabled="props.loading" @click="emit('enter')">进入工作台</button>
    </header>

    <section class="overview-status" aria-label="项目状态">
      <article class="overview-card binding-card">
        <div class="overview-card-heading"><span>目录绑定</span><button type="button" class="quiet-action" @click="emit('binding')">编辑绑定</button></div>
        <div v-if="props.tree" class="binding-pair">
          <span><small>源码</small><code>{{ props.tree.binding.source_root }}</code></span>
          <span class="binding-arrow" aria-hidden="true">→</span>
          <span><small>文档</small><code>{{ props.tree.binding.docs_root }}</code></span>
        </div>
        <p v-else class="overview-muted">{{ props.loading ? '正在读取项目目录…' : '项目目录不可用' }}</p>
        <div class="overview-metrics">
          <span>{{ props.tree?.sources.length || 0 }} 个源码文件</span>
          <span>{{ props.tree?.documents.length || 0 }} 份 Markdown</span>
          <span v-if="missingCount" class="warning-text">{{ missingCount }} 份待生成</span>
          <span v-if="orphanCount" class="error-text">{{ orphanCount }} 份孤立文档</span>
        </div>
      </article>

      <article class="overview-card">
        <div class="overview-card-heading"><span>Agent</span><button type="button" class="quiet-action" @click="emit('settings')">设置</button></div>
        <strong :data-state="props.settings?.configured ? 'success' : 'warning'">
          {{ props.settings?.configured ? '模型已配置' : '可用本地解析' }}
        </strong>
        <p class="overview-muted">
          {{ props.settings?.allow_source_upload ? '项目内容读取已开启' : '项目内容读取已关闭' }}
        </p>
      </article>

      <article class="overview-card">
        <div class="overview-card-heading"><span>Git</span><span class="overview-dot" :data-state="props.git?.is_repo ? 'success' : 'warning'"></span></div>
        <strong>{{ props.git?.is_repo ? (props.git.branch || 'detached') : '未初始化' }}</strong>
        <p class="overview-muted">
          {{ props.git?.is_repo ? `${worktreeCount} 项工作区变更 · ${props.markdown?.changed_documents || 0} 份 Markdown` : '问答与同步仍可使用；计划和提交需要 Git 基线' }}
        </p>
      </article>

      <article class="overview-card">
        <div class="overview-card-heading"><span>待处理</span><button v-if="pendingCount" type="button" class="quiet-action" @click="emit('review')">查看</button></div>
        <strong>{{ pendingCount }} 项</strong>
        <p class="overview-muted">草稿、提案或 Markdown 变更</p>
      </article>
    </section>

    <section class="overview-work" aria-labelledby="overviewWorkTitle">
      <div class="overview-section-heading">
        <div><p class="eyebrow">START A TASK</p><h2 id="overviewWorkTitle">选择工作</h2></div>
      </div>
      <div class="task-grid">
        <button type="button" class="task-card" @click="emit('ask')">
          <span class="task-index">01</span><strong>浏览与问答</strong><small>查找代码和文档，查看实现链路</small>
        </button>
        <button type="button" class="task-card task-card-primary" @click="emit('sync')">
          <span class="task-index">02</span><strong>同步代码文档</strong><small>生成源码镜像文档，审核 Diff 后写入</small>
        </button>
        <button type="button" class="task-card" @click="emit('document')">
          <span class="task-index">03</span><strong>新建或修改文档</strong><small>生成 Markdown 提案，逐份审核</small>
        </button>
        <button type="button" class="task-card" :disabled="!props.git?.is_repo || !props.git?.head_commit" @click="emit('plan')">
          <span class="task-index">04</span><strong>制定功能计划</strong><small>需求、架构、测试计划与外部交接</small>
        </button>
      </div>
    </section>

    <footer class="overview-footer">
      <span>写入文档只更新工作区；提交操作会显示文件清单和 Diff，并等待确认。</span>
      <button type="button" class="quiet-action" @click="emit('review')">待审与提交 <span v-if="pendingCount">{{ pendingCount }}</span></button>
    </footer>
  </main>
</template>
