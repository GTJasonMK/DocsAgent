<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue';
import type { ChatMessage, ExecutionState, Session } from '../types';

const props = defineProps<{
  sessionId: string;
  sessions: Session[];
  messages: ChatMessage[];
  execution: ExecutionState;
  executionOpen: boolean;
  busy: boolean;
  sessionBusy: boolean;
  syncing: boolean;
  workflow?: 'document' | null;
}>();
const emit = defineEmits<{
  send: [message: string];
  sessionChange: [sessionId: string];
  newSession: [];
  renameSession: [];
  clearChat: [];
  deleteSession: [];
  executionOpen: [open: boolean];
  cancelTask: [];
}>();

const message = ref('');
const chatElement = ref<HTMLElement | null>(null);
const composerElement = ref<HTMLTextAreaElement | null>(null);
const sessionMenu = ref<HTMLDetailsElement | null>(null);
const currentSession = computed(() => props.sessions.find((item) => item.id === props.sessionId) || null);
const controlsDisabled = computed(() => props.busy || props.sessionBusy);
const sessionLabel = computed(() => {
  if (!currentSession.value) return '无会话';
  return currentSession.value.message_count
    ? Math.ceil(currentSession.value.message_count / 2) + ' 轮对话'
    : '空会话';
});

watch(() => props.messages.length, async () => {
  await nextTick();
  if (chatElement.value) chatElement.value.scrollTop = chatElement.value.scrollHeight;
});

function submit() {
  if (props.busy || props.syncing) return;
  const value = message.value.trim();
  if (!value) return;
  message.value = '';
  emit('send', value);
}

function focusComposer() {
  composerElement.value?.focus();
}

defineExpose({ focusComposer });

function closeMenuAndEmit(action: 'renameSession' | 'clearChat' | 'deleteSession') {
  sessionMenu.value?.removeAttribute('open');
  if (action === 'renameSession') emit('renameSession');
  else if (action === 'clearChat') emit('clearChat');
  else emit('deleteSession');
}
</script>

<template>
  <aside id="chatPanel" class="sidebar chat-panel">
    <div class="panel-title chat-heading">
      <div class="chat-title"><span>Agent</span><small>{{ sessionLabel }}</small></div>
      <div class="session-controls">
        <label class="sr-only" for="sessionSelect">当前会话</label>
        <select id="sessionSelect" :value="props.sessionId" title="切换会话" :disabled="controlsDisabled || !props.sessions.length" @change="emit('sessionChange', ($event.target as HTMLSelectElement).value)">
          <option v-for="session in props.sessions" :key="session.id" :value="session.id">{{ session.title }}{{ session.pending_proposals ? ' · ' + session.pending_proposals + ' 待审' : '' }}</option>
        </select>
        <button type="button" class="icon-button" aria-label="新建会话" title="新建会话" :disabled="controlsDisabled" @click="emit('newSession')">+</button>
        <details id="sessionMenu" ref="sessionMenu" class="session-menu" :class="{ disabled: controlsDisabled }">
          <summary aria-label="会话操作" title="会话操作" :aria-disabled="controlsDisabled" :tabindex="controlsDisabled ? -1 : 0" @click="controlsDisabled && $event.preventDefault()">•••</summary>
          <div class="session-menu-popover">
            <button type="button" :disabled="controlsDisabled || !currentSession" @click="closeMenuAndEmit('renameSession')">重命名</button>
            <button type="button" :disabled="controlsDisabled || !currentSession" @click="closeMenuAndEmit('clearChat')">清空消息</button>
            <button type="button" class="destructive-text" :disabled="controlsDisabled || !currentSession || props.sessionId === 'default'" @click="closeMenuAndEmit('deleteSession')">删除会话</button>
          </div>
        </details>
      </div>
    </div>
    <div ref="chatElement" id="chat" class="chat" aria-live="polite" aria-label="Agent 会话">
      <div v-for="item in props.messages" :key="item.id" class="message" :class="[item.role, { pending: item.pending }]">{{ item.content }}</div>
    </div>
    <section id="execution" class="execution" :data-state="props.execution.state" aria-label="Agent 执行状态">
      <div class="execution-header">
        <div class="execution-title"><span class="execution-kicker">执行过程</span><strong>{{ props.execution.title }}</strong></div>
        <div class="execution-actions">
          <span class="execution-meta">{{ props.execution.meta }}</span>
          <button v-if="props.busy" type="button" class="quiet-action" @click="emit('cancelTask')">取消</button>
        </div>
      </div>
      <div class="execution-current" role="status" aria-live="polite">{{ props.execution.current }}</div>
      <details class="execution-details" :open="props.executionOpen" @toggle="emit('executionOpen', ($event.target as HTMLDetailsElement).open)">
        <summary>执行记录 <span>{{ props.execution.steps.length }}</span></summary>
        <ol>
          <li v-for="step in props.execution.steps" :key="step.id" :data-state="step.state">
            <span class="execution-step-label">{{ step.label }}</span><span class="execution-step-detail">{{ step.detail }}</span>
          </li>
        </ol>
      </details>
    </section>
    <form class="chat-form" @submit.prevent="submit">
      <label class="sr-only" for="message">Agent 消息</label>
      <textarea id="message" ref="composerElement" v-model="message" rows="3" :placeholder="props.workflow === 'document' ? '描述要新建或修改的 Markdown 文档' : '询问项目或描述文档任务'" :disabled="props.busy" />
      <button class="primary" type="submit" :disabled="props.busy || props.syncing">发送</button>
    </form>
  </aside>
</template>
