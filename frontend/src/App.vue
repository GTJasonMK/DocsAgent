<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';
import ChatPanel from './components/ChatPanel.vue';
import EditorPane from './components/EditorPane.vue';
import FeaturePlanDialog from './components/FeaturePlanDialog.vue';
import ProjectOverview from './components/ProjectOverview.vue';
import ProjectExplorer from './components/ProjectExplorer.vue';
import ReviewCenter from './components/ReviewCenter.vue';
import { post, request } from './api';
import { escapeText, renderDiff, renderMarkdown, streamingDiff } from './render';
import type {
  AgentSettings, BindingPreview, ChatEvent, ChatMessage, ConversationMessage,
  ExecutionState, FeaturePlanDiff, FeaturePlanRequest, GitLogItem, GitStatus, GraphResponse,
  MarkdownStatus, PendingWork, Proposal, Session, SettingsPayload, StatusKind, SyncEvent,
  TreeItem, TreeResponse, ViewMode,
} from './types';

const initialSessionId = (() => {
  try {
    return localStorage.getItem('docsagent-session-id') || 'default';
  } catch {
    return 'default';
  }
})();
const desktopAvailable = Boolean(window.docsAgentDesktop);

const tree = ref<TreeResponse | null>(null);
const git = ref<GitStatus | null>(null);
const markdownStatus = ref<MarkdownStatus | null>(null);
const agentStatus = ref<AgentSettings | null>(null);
const pendingWork = ref<PendingWork | null>(null);
const workspaceMode = ref<'overview' | 'workbench'>('overview');
const reviewOpen = ref(false);
const featurePlanOpen = ref(false);
const chatWorkflow = ref<'document' | null>(null);
const leaveRequestCleanup = ref<(() => void) | null>(null);
const reviewCenter = ref<InstanceType<typeof ReviewCenter> | null>(null);
const tabs = ref<string[]>([]);
const current = ref<string | null>(null);
const view = ref<ViewMode>('preview');
const documents = reactive(new Map<string, string>());
const bases = reactive(new Map<string, string>());
const drafts = reactive<Record<string, string>>({});
const sources = reactive(new Map<string, string>());
const sourcesReady = reactive(new Set<string>());
const diffs = reactive(new Map<string, string>());
const sourcePath = ref<string | null>(null);
const sourceOnlyPath = ref<string | null>(null);
const follow = ref(false);
const taskId = ref<string | null>(null);
const specialContent = ref<string | null>(null);
const status = ref('就绪');
const statusState = ref<StatusKind | ''>('');
const syncing = ref(false);
const writeReady = ref(false);
const writeBusy = ref(false);
const bindingPreview = ref<BindingPreview | null>(null);
const bindingPreviewBusy = ref(false);
const bindingPreviewError = ref('');
const chatBusy = ref(false);
const chatTaskId = ref<string | null>(null);
const sessionBusy = ref(false);
const chatStream = ref<EventSource | null>(null);
const syncStream = ref<EventSource | null>(null);
const sessionId = ref(initialSessionId);
const sessions = ref<Session[]>([]);
const messages = ref<ChatMessage[]>([]);
let messageSequence = 0;
let fileSelectionSequence = 0;

const execution = reactive<ExecutionState>({
  state: 'idle',
  title: '空闲',
  meta: '等待任务',
  current: '等待任务',
  steps: [],
});
const executionOpen = ref(false);
const explorerOpen = ref(false);
const chatOpen = ref(false);

const bindingDialog = ref<HTMLDialogElement | null>(null);
const bindingSource = ref('');
const bindingDocs = ref('');
const bindingBusy = ref(false);
const bindingMessage = ref('');
const bindingMessageState = ref<StatusKind | ''>('');

const actionDialog = ref<HTMLDialogElement | null>(null);
const actionInput = ref<HTMLInputElement | null>(null);
const action = reactive({
  title: '确认操作',
  message: '',
  confirmText: '确认',
  inputLabel: '',
  inputValue: '',
  destructive: false,
  error: '',
});
let actionResolve: ((value: boolean | string | null) => void) | null = null;

const settingsDialog = ref<HTMLDialogElement | null>(null);
const settingsMessage = ref('');
const settingsMessageState = ref<StatusKind | ''>('');
const settingsBusy = ref(false);
const validationCommandsText = ref('[]');
const mcpServersText = ref('[]');
const memoryEnabled = ref(false);
const settings = reactive<SettingsPayload>({
  model_url: '',
  model: '',
  request_format: 'openai_chat',
  endpoint: '',
  temperature: 0.2,
  max_tokens: 2048,
  instructions: '',
  allow_source_upload: false,
  validation_commands: [],
  memory_enabled: false,
  api_key: '',
  clear_api_key: false,
});
const formatHints: Record<AgentSettings['request_format'], [string, string]> = {
  openai_chat: ['https://api.openai.com/v1', '留空使用 /chat/completions'],
  openai_responses: ['https://api.openai.com/v1', '留空使用 /responses'],
  anthropic_messages: ['https://api.anthropic.com', '留空使用 /v1/messages'],
  ollama_chat: ['http://localhost:11434', '留空使用 /api/chat'],
  gemini_generate: ['https://generativelanguage.googleapis.com', '留空使用 /v1beta/models/{model}:generateContent'],
  openai_compatible: ['http://localhost:8000/v1', '留空使用 /chat/completions'],
};
const settingUrlPlaceholder = computed(() => formatHints[settings.request_format][0]);
const settingEndpointPlaceholder = computed(() => formatHints[settings.request_format][1]);

const proposalDialog = ref<HTMLDialogElement | null>(null);
const proposals = ref<Proposal[]>([]);
const proposalEdits = reactive<Record<string, string>>({});
const selectedProposalId = ref<string | null>(null);
const proposalContent = ref('');
const proposalValidation = ref('');
const proposalValidationState = ref<StatusKind | ''>('');
const proposalBusy = ref(false);
const proposalCanAccept = ref(true);
let proposalPreviewVersion = 0;
let proposalPreviewTimer: number | undefined;
const selectedProposal = computed(() => proposals.value.find((item) => item.proposal_id === selectedProposalId.value) || null);

const binding = computed(() => tree.value?.binding || null);
const sourceAvailable = computed(() => Boolean(sourcePath.value && sourcesReady.has(sourcePath.value)));
const sourceTitle = computed(() => sourceAvailable.value
  ? '查看绑定的源码文件'
  : current.value && sourcePathFor(current.value)
    ? '绑定源码不存在或无法读取'
    : '当前文档没有绑定源码');
const displayPath = computed(() => current.value || sourceOnlyPath.value);
const currentFeatureId = computed(() => {
  const match = displayPath.value?.match(/^docs\/features\/([a-z0-9]+(?:-[a-z0-9]+)*)\/(?:requirements|architecture|plan)\.md$/);
  return match?.[1] || null;
});
const gitStatusText = computed(() => {
  if (!git.value) return 'Git 状态不可用';
  if (!git.value.is_repo) return '未初始化 Git';
  const docsRoot = binding.value?.docs_root || '';
  const prefix = docsRoot.replace(/\/$/, '') + '/';
  const count = (git.value.entries || []).filter((item) => item.path === docsRoot || item.path.startsWith(prefix)).length;
  return (git.value.branch || 'detached') + ' · ' + count + ' 文档变更';
});
const contentHtml = computed(() => {
  if (specialContent.value !== null) return specialContent.value;
  const path = current.value;
  const content = path ? documents.get(path) || '' : '';
  const source = sourcePath.value ? sources.get(sourcePath.value) || '正在读取源码...' : '当前文档没有绑定源码。';
  if (!path && !sourceOnlyPath.value) {
    return '<div class="empty-state"><div class="empty-symbol">⌘</div><h1>选择一个文档</h1><p><span class="desktop-empty-hint">从左侧目录打开 Markdown，或同步源码文档开始工作。</span><span class="mobile-empty-hint">点顶部“目录”打开 Markdown，或同步源码文档开始工作。</span></p></div>';
  }
  if (view.value === 'preview') return '<div class="markdown">' + renderMarkdown(content) + '</div>';
  if (view.value === 'source') return '<pre class="source-view">' + escapeText(source) + '</pre>';
  if (view.value === 'split') {
    return '<div class="split"><section class="split-pane"><header class="split-path"><span>文档</span><code>' + escapeText(path || '') + '</code></header><div class="markdown">' + renderMarkdown(content) + '</div></section><section class="split-pane"><header class="split-path"><span>源码</span><code>' + escapeText(sourcePath.value || '') + '</code></header><pre class="source-view">' + escapeText(source) + '</pre></section></div>';
  }
  const diff = path ? diffs.get(path) || streamingDiff(bases.get(path) || '', content) : '';
  return renderDiff(diff);
});
const proposalDiffHtml = ref('');
const proposalCount = computed(() => proposals.value.length);
const chatPanel = ref<InstanceType<typeof ChatPanel> | null>(null);
const proposalEditCount = computed(() => Object.keys(proposalEdits).filter((id) => {
  const proposal = proposals.value.find((item) => item.proposal_id === id);
  return proposal && proposalEdits[id] !== proposal.content;
}).length);
const pendingLeaveItems = computed(() => {
  const items: string[] = [];
  if (syncing.value) items.push('正在运行的代码文档同步');
  const syncDraftCount = Math.max(Object.keys(drafts).length, pendingWork.value?.sync_files.length || 0);
  const proposalCountAll = Math.max(proposalCount.value, pendingWork.value?.proposal_paths.length || 0);
  if (syncDraftCount) items.push(syncDraftCount + ' 个未写入同步草稿');
  if (proposalCountAll) items.push(proposalCountAll + ' 份待审提案');
  if (proposalEditCount.value) items.push(proposalEditCount.value + ' 份尚未保存的提案编辑');
  const messagesCount = pendingWork.value?.message_count || 0;
  if (messagesCount) items.push('当前进程内的聊天记录');
  if (chatBusy.value) items.push('正在运行的 Agent 请求');
  return items;
});
const hasPendingLeave = computed(() => pendingWork.value === null || pendingLeaveItems.value.length > 0 || Boolean(pendingWork.value?.has_pending));
const bindingMatchesPreview = computed(() => Boolean(bindingPreview.value
  && bindingPreview.value.tree.binding.source_root === bindingSource.value.trim()
  && bindingPreview.value.tree.binding.docs_root === bindingDocs.value.trim()));

function setStatus(message: string, state: StatusKind | '' = '') {
  status.value = message;
  statusState.value = state;
}

function showOperationError(title: string, error: unknown) {
  setStatus(title + '：' + friendlyError(error), 'error');
}

function friendlyError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  if (/not a git repository/i.test(message)) return '当前项目尚未初始化 Git，请先在项目目录执行 git init。';
  if (/git executable not found|git command not found|enoent/i.test(message)) return '当前环境未安装 Git，或 Git 不在 PATH 中。';
  if (/\b401\b|missing api key|invalid_api_key|api key/i.test(message)) return '模型服务拒绝了请求：未检测到有效 API Key。请打开“Agent 设置”重新填写，或在启动服务前设置 DOCSAGENT_API_KEY。保存后的 Key 会按项目保存在本机用户配置中。';
  if (/maximum of \d+ tool rounds|执行轮次上限|maximum.*tool rounds/i.test(message)) return 'Agent 已达到本次请求的执行轮次上限，已有证据已保留。请缩小问题范围，或把任务拆成两次提问。';
  return message;
}

function setView(next: ViewMode) {
  view.value = next;
  specialContent.value = null;
}

function resetExecution(message = '等待任务') {
  execution.steps.splice(0);
  execution.state = 'idle';
  execution.title = '空闲';
  execution.meta = '等待任务';
  execution.current = message;
}

function setExecutionState(stateName: StatusKind, title: string, meta: string, currentText?: string) {
  execution.state = stateName;
  execution.title = title;
  execution.meta = meta || '';
  if (currentText) execution.current = currentText;
}

function addExecutionStep(id: string, label: string, detail = '', stateName: StatusKind = 'running') {
  const key = id || 'step-' + (execution.steps.length + 1);
  const existing = execution.steps.find((item) => item.id === key);
  if (existing) {
    existing.label = label;
    existing.detail = detail;
    existing.state = stateName;
  } else {
    execution.steps.push({ id: key, label, detail, state: stateName });
  }
}

function executionToolLabel(tool = ''): string {
  const labels: Record<string, string> = {
    list_project_files: '列出项目文件',
    search_project: '搜索项目内容',
    read_project_file: '读取项目文件',
    edit_project_file: '修改项目文件',
    run_project_command: '运行项目命令',
    search_web: '搜索网页',
    read_web_page: '读取网页',
    browser_read_page: '浏览器读取网页',
    inspect_project_attachment: '分析项目附件',
    list_document_paths: '列出文档',
    read_document_sections: '读取文档段落',
    create_document_plan: '建立文档计划',
    document_task_file: '提交文档草稿',
    patch_markdown_documents: '生成文档修改',
    complete_document_task: '提交文档提案',
  };
  return labels[tool] || tool || '工具调用';
}

function handleExecutionEvent(data: ChatEvent) {
  if (data.event === 'status') {
    const stateName: StatusKind = data.phase === 'error' ? 'error' : data.phase === 'completed' ? 'success' : 'running';
    const title = stateName === 'error' ? '执行失败' : stateName === 'success' ? '已完成' : '执行中';
    const phaseLabels: Record<string, string> = {
      starting: '请求', context: '上下文', search: '项目搜索', model: '模型', finalizing: '收尾', completed: 'Agent', error: '错误',
    };
    setExecutionState(stateName, title, phaseLabels[data.phase || ''] || execution.meta, data.message || '正在处理…');
    addExecutionStep('status-' + execution.steps.length + '-' + (data.phase || 'step'), data.message || '更新执行状态', '', stateName);
  } else if (data.event === 'round') {
    setExecutionState('running', '执行中', '第 ' + data.round + '/' + data.max_rounds + ' 轮 · 工具 ' + data.tool_calls + '/' + data.max_tool_calls, '正在规划下一步…');
  } else if (data.event === 'tool_started') {
    setExecutionState('running', '执行中', execution.meta, '正在' + executionToolLabel(data.tool) + '…');
    addExecutionStep(data.call_id || '', executionToolLabel(data.tool), data.summary ? '调用参数：' + data.summary : '正在执行', 'running');
  } else if (data.event === 'tool_finished') {
    const stateName = data.ok ? 'success' : 'error';
    addExecutionStep(data.call_id || '', executionToolLabel(data.tool) + (data.ok ? ' · 完成' : ' · 失败'), data.summary || '', stateName);
  } else if (data.event === 'artifact' && data.kind === 'file_diff') {
    specialContent.value = renderDiff(data.diff || '');
    view.value = 'diff';
    addExecutionStep('artifact-' + (data.path || ''), '代码修改 Diff', data.path || '', 'success');
  } else if (data.event === 'approval_required') {
    setExecutionState('warning', '等待确认', '命令审批', 'Agent 请求运行一条未加入白名单的命令');
  }
}

function clearMap(map: Map<string, string>) {
  map.clear();
}

function clearRecord(record: Record<string, string>) {
  Object.keys(record).forEach((key) => delete record[key]);
}

function persistSessionId() {
  try {
    localStorage.setItem('docsagent-session-id', sessionId.value);
  } catch {
    // Browser storage can be unavailable.
  }
}

function selectedSession(): Session | null {
  return sessions.value.find((item) => item.id === sessionId.value) || null;
}

async function loadSessions(): Promise<Session | null> {
  const data = await request<{ sessions: Session[] }>('/api/chat/sessions');
  sessions.value = Array.isArray(data.sessions) ? data.sessions : [];
  if (!sessions.value.some((item) => item.id === sessionId.value)) {
    const fallback = sessions.value.find((item) => item.id === 'default') || sessions.value[0];
    if (fallback) {
      sessionId.value = fallback.id;
      persistSessionId();
    }
  }
  return selectedSession();
}

async function loadChatHistory() {
  const data = await request<{ messages: ConversationMessage[] }>('/api/chat/history?session_id=' + encodeURIComponent(sessionId.value));
  messages.value = data.messages.map((item) => ({
    id: ++messageSequence,
    role: item.role === 'user' ? 'user' : 'agent',
    content: item.content,
  }));
}

async function refreshProposals() {
  const data = await request<{ proposals: Proposal[] }>('/api/chat/proposals?all_sessions=true');
  proposals.value = data.proposals;
  const activeIds = new Set(proposals.value.map((item) => item.proposal_id));
  Object.keys(proposalEdits).forEach((id) => {
    if (!activeIds.has(id)) delete proposalEdits[id];
  });
  if (!proposals.value.some((item) => item.proposal_id === selectedProposalId.value)) {
    selectedProposalId.value = proposals.value[0]?.proposal_id || null;
  }
}

async function reloadSessionWorkspace() {
  if (proposalPreviewTimer) clearTimeout(proposalPreviewTimer);
  proposalPreviewVersion += 1;
  messages.value = [];
  resetExecution();
  await loadChatHistory();
  await refreshProposals();
}

async function switchSession(nextId: string) {
  if (chatBusy.value || sessionBusy.value) {
    setStatus('当前 Agent 请求完成后才能切换会话', 'warning');
    return;
  }
  if (!sessions.value.some((item) => item.id === nextId)) return;
  sessionBusy.value = true;
  try {
    sessionId.value = nextId;
    persistSessionId();
    await reloadSessionWorkspace();
    setStatus('已切换到会话：' + (selectedSession()?.title || '新会话'));
  } catch (error) {
    showOperationError('切换会话失败', error);
  } finally {
    sessionBusy.value = false;
  }
}

function sourcePathFor(documentPath: string): string | null {
  if (!binding.value) return null;
  const docsRoot = binding.value.docs_root === '.' ? '' : binding.value.docs_root.replace(/\/$/, '');
  const prefix = docsRoot ? docsRoot + '/' : '';
  if (!documentPath.startsWith(prefix)) return null;
  const source = documentPath.slice(prefix.length).replace(/\.md$/, '');
  const sourceRoot = binding.value.source_root.replace(/\/$/, '');
  if (sourceRoot && sourceRoot !== '.' && source !== sourceRoot && !source.startsWith(sourceRoot + '/')) return null;
  return tree.value?.sources.some((item) => item.path === source) ? source : null;
}

function documentPathForSource(path: string): string | null {
  if (!binding.value) return null;
  const sourceRoot = binding.value.source_root.replace(/\/$/, '');
  if (sourceRoot && sourceRoot !== '.' && path !== sourceRoot && !path.startsWith(sourceRoot + '/')) return null;
  const docsRoot = binding.value.docs_root === '.' ? '' : binding.value.docs_root.replace(/\/$/, '');
  return [docsRoot, path + '.md'].filter(Boolean).join('/');
}

async function ensureSource(documentPath: string): Promise<boolean> {
  const path = sourcePathFor(documentPath);
  sourcePath.value = path;
  sourceOnlyPath.value = null;
  if (!path) return false;
  if (sourcesReady.has(path)) return true;
  try {
    const data = await request<{ content: string }>('/api/source?path=' + encodeURIComponent(path));
    sources.set(path, data.content);
    sourcesReady.add(path);
    return true;
  } catch (error) {
    sources.set(path, '绑定源码无法读取：' + friendlyError(error));
    return false;
  }
}

async function loadDocument(path: string) {
  const selection = ++fileSelectionSequence;
  current.value = path;
  sourceOnlyPath.value = null;
  sourcePath.value = sourcePathFor(path);
  specialContent.value = null;
  if (!documents.has(path)) {
    try {
      const data = await request<{ content: string }>('/api/document?path=' + encodeURIComponent(path));
      documents.set(path, data.content);
    } catch (error) {
      if (selection === fileSelectionSequence) throw error;
      return;
    }
  }
  if (selection !== fileSelectionSequence || current.value !== path) return;
  await ensureSource(path);
}

async function openTab(path: string) {
  if (!tabs.value.includes(path)) tabs.value.push(path);
  current.value = path;
  setView('preview');
  await loadDocument(path);
}

async function openSource(path: string) {
  const selection = ++fileSelectionSequence;
  try {
    const data = await request<{ content: string }>('/api/source?path=' + encodeURIComponent(path));
    sources.set(path, data.content);
    sourcesReady.add(path);
    if (selection !== fileSelectionSequence) return;
    sourcePath.value = path;
    const pairedDocument = documentPathForSource(path);
    if (pairedDocument && tree.value?.documents.some((item) => item.path === pairedDocument)) {
      if (!tabs.value.includes(pairedDocument)) tabs.value.push(pairedDocument);
      await loadDocument(pairedDocument);
      if (current.value !== pairedDocument) return;
      sourcePath.value = path;
      setView('split');
      return;
    }
    current.value = null;
    sourceOnlyPath.value = path;
    setView('source');
  } catch (error) {
    if (selection !== fileSelectionSequence) return;
    showOperationError('无法读取源码', error);
  }
}

function changeView(next: ViewMode) {
  setView(next);
  if (next === 'source' && current.value) {
    void ensureSource(current.value);
  }
}

function onContentClick(event: MouseEvent) {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  const graphAll = target.closest<HTMLElement>('[data-graph-all]');
  if (graphAll) {
    void showGraph(true).catch((error) => showOperationError('读取实现链路失败', error));
    return;
  }
  const link = target.closest<HTMLAnchorElement>('[data-doc-link]');
  const targetPath = link?.dataset.docLink;
  if (!link || !targetPath || targetPath.startsWith('#')) return;
  event.preventDefault();
  const parts = targetPath.split('#', 2);
  const documentPath = parts[0];
  const anchor = parts[1];
  const base = documentPath.startsWith('/') ? [] : (current.value || '').split('/').slice(0, -1);
  documentPath.split('/').forEach((part) => {
    if (!part || part === '.') return;
    if (part === '..') base.pop();
    else base.push(part);
  });
  void openTab(base.join('/')).then(async () => {
    if (anchor) {
      await nextTick();
      document.getElementById(anchor)?.scrollIntoView({ block: 'start' });
    }
  }).catch((error) => showOperationError('无法打开链接文档', error));
}

async function refresh() {
  tree.value = await request<TreeResponse>('/api/tree');
  await Promise.all([loadGit(), loadWorkspacePending()]);
}

async function loadGit() {
  try {
    const [gitStatus, markdown] = await Promise.all([
      request<GitStatus>('/api/git/status'),
      request<MarkdownStatus>('/api/git/markdown/status'),
    ]);
    git.value = gitStatus;
    markdownStatus.value = markdown;
  } catch (error) {
    git.value = null;
    markdownStatus.value = null;
    showOperationError('读取 Git 状态失败', error);
  }
}

function showGitUnavailable() {
  setStatus('项目尚未初始化 Git；浏览与同步仍可使用', 'warning');
}

async function loadAgentStatus() {
  try {
    agentStatus.value = await request<AgentSettings>('/api/settings');
  } catch (error) {
    agentStatus.value = null;
    showOperationError('读取 Agent 状态失败', error);
  }
}

async function loadWorkspacePending() {
  try {
    pendingWork.value = await request<PendingWork>('/api/workspace/pending');
  } catch (error) {
    showOperationError('读取待处理状态失败', error);
  }
}

function showGitError(title: string, error: unknown) {
  specialContent.value = '<div class="empty-state"><h2>' + escapeText(title) + '</h2><p>' + escapeText(friendlyError(error)) + '</p></div>';
  showOperationError(title, error);
}

async function showGitDiff() {
  if (!git.value?.is_repo) {
    showGitUnavailable();
    return;
  }
  setView('diff');
  try {
    const docsRoot = binding.value?.docs_root || '';
    const data = await request<{ diff: string; truncated: boolean }>('/api/git/diff?path=' + encodeURIComponent(docsRoot));
    specialContent.value = (data.truncated ? '<p class="warning-note">差异内容已截断（最多显示 50,000 个字符）。</p>' : '') + renderDiff(data.diff || '当前绑定目录没有 Git diff');
  } catch (error) {
    showGitError('无法加载 Git Diff', error);
  }
}

async function showGitHistory() {
  if (!git.value?.is_repo) {
    showGitUnavailable();
    return;
  }
  setView('preview');
  try {
    const data = await request<{ items: GitLogItem[] }>('/api/git/log');
    const items = data.items.map((item) => '<p><code>' + escapeText(item.commit.slice(0, 8)) + '</code> ' + escapeText(item.date) + ' ' + escapeText(item.subject) + '</p>').join('');
    specialContent.value = '<div class="markdown"><h2>文档 Git 历史</h2>' + (items || '<p>没有文档提交记录。</p>') + '</div>';
  } catch (error) {
    showGitError('无法加载 Git 历史', error);
  }
}

function requestAction(options: {
  title: string;
  message: string;
  confirmText?: string;
  inputLabel?: string;
  inputValue?: string;
  destructive?: boolean;
}): Promise<boolean | string | null> {
  action.title = options.title;
  action.message = options.message;
  action.confirmText = options.confirmText || '确认';
  action.inputLabel = options.inputLabel || '';
  action.inputValue = options.inputValue || '';
  action.destructive = Boolean(options.destructive);
  action.error = '';
  return new Promise((resolve) => {
    actionResolve = resolve;
    nextTick(() => {
      if (actionDialog.value && !actionDialog.value.open) actionDialog.value.showModal();
      if (action.inputLabel) actionInput.value?.focus();
    });
  });
}

function finishAction(confirmed: boolean) {
  if (!actionResolve) return;
  if (confirmed && action.inputLabel && !action.inputValue.trim()) {
    action.error = '请填写提交信息';
    actionInput.value?.focus();
    return;
  }
  const resolve = actionResolve;
  actionResolve = null;
  actionDialog.value?.close();
  resolve(confirmed ? (action.inputLabel ? action.inputValue.trim() : true) : null);
}

async function commitDocs(paths: string[], expectedDiffDigest: string) {
  if (!git.value?.is_repo) {
    showGitUnavailable();
    return;
  }
  if (!paths.length) {
    setStatus('没有选择 Markdown 文件');
    return;
  }
  const message = await requestAction({
    title: '提交文档',
    message: '将提交以下文档：\n\n' + paths.join('\n'),
    confirmText: '提交',
    inputLabel: '提交信息',
    inputValue: 'docs: update documentation',
  });
  if (typeof message !== 'string' || !message) return;
  const data = await post<{ result: string }>('/api/git/commit', {
    confirm: true,
    message,
    paths,
    expected_diff_digest: expectedDiffDigest,
  });
  setStatus(data.result || '文档已提交');
  await refresh();
  await reviewCenter.value?.refresh();
}

async function commitFeaturePlan(featureId = currentFeatureId.value) {
  if (!featureId) return;
  if (!git.value?.is_repo) {
    showGitUnavailable();
    return;
  }
  const preview = await request<FeaturePlanDiff>('/api/git/feature-plan/diff?feature_id=' + encodeURIComponent(featureId));
  setView('diff');
  specialContent.value = (preview.truncated ? '<p class="warning-note">差异内容已截断（最多显示 50,000 个字符）。</p>' : '') + renderDiff(preview.diff || '当前功能计划没有未提交差异');
  if (!preview.files.length) {
    setStatus('当前功能计划没有待提交修改');
    return;
  }
  if (!preview.valid) {
    setStatus('功能文档包未通过校验：' + preview.errors.join('；'), 'error');
    return;
  }
  if (!preview.baseline_matches_head) {
    setStatus('功能计划基线已变化；请重新生成计划后再提交', 'error');
    return;
  }
  if (preview.truncated) {
    setStatus('功能计划 Diff 已截断；请缩小计划范围后再提交', 'error');
    return;
  }
  const message = await requestAction({
    title: '提交功能计划',
    message: '将只提交以下功能文档：\n\n' + preview.files.join('\n'),
    confirmText: '提交计划',
    inputLabel: '提交信息',
    inputValue: 'docs(feature): plan ' + featureId,
  });
  if (typeof message !== 'string' || !message) return;
  const result = await post<{ result: string }>('/api/git/feature-plan/commit', {
    confirm: true,
    feature_id: featureId,
    message,
    expected_diff_digest: preview.digest,
  });
  setStatus(result.result || '功能计划已提交');
  await refresh();
  await reviewCenter.value?.refresh();
}

function graphEvidenceLabel(value: string): string {
  const labels: Record<string, string> = {
    static: '静态解析', heuristic: '启发式解析', 'unique-project-name': '项目内唯一名称匹配',
    inferred: '推断关系', external: '外部依赖',
  };
  return labels[value] || '解析器证据';
}

function graphKindLabel(value: string): string {
  const labels: Record<string, string> = {
    bind: '界面事件', calls: '函数调用', renders: '组件渲染', updates: '状态更新',
    requests: '请求边界', routes: '路由关联',
  };
  return labels[value] || '调用关系';
}

function graphDiagnosticLabel(value: string): string {
  if (value.startsWith('heuristic parser:')) return '此文件使用启发式解析，声明、参数和调用链可能不完整。';
  if (value === 'generic parser found no declarations') return '未识别到可解析的定义。';
  if (value.startsWith('syntax error at line ')) return '源码存在语法错误：' + value.slice('syntax error at line '.length);
  return value;
}

async function showGraph(all = false) {
  const docsRoot = binding.value?.docs_root || '';
  const path = !all && current.value && binding.value && current.value.startsWith(docsRoot + '/')
    ? current.value.slice((docsRoot + '/').length).replace(/\.md$/, '')
    : null;
  const params = new URLSearchParams({ depth: '3' });
  if (path && binding.value) params.set('path', binding.value.source_root + '/' + path);
  const data = await request<GraphResponse>('/api/graph?' + params.toString());
  const nodes = new Map(data.nodes.map((node) => [node.id, node]));
  const edges = data.edges || [];
  const resolved = edges.filter((edge) => edge.status === 'resolved').length;
  const diagnostics = (data.diagnostics || []).map((item) => {
    const messages = item.messages.map(graphDiagnosticLabel).map(escapeText).join('；');
    return '<li><code>' + escapeText(item.path) + '</code>：' + messages + '</li>';
  }).join('');
  const link = (node: GraphResponse['nodes'][number]) => {
    const label = escapeText(node.label);
    if (!node.document || !node.anchor) return label;
    const target = '/' + node.document + '#' + node.anchor;
    return '<a href="' + escapeText(target) + '" data-doc-link="' + escapeText(target) + '">' + label + '</a>';
  };
  const rows = edges.map((edge) => {
    const from = nodes.get(edge.from);
    const to = nodes.get(edge.to);
    if (!from || !to) return '';
    return '<div class="graph-node"><strong>' + link(from) + ' <span aria-hidden="true">→</span> ' + link(to) + '</strong><div class="muted">' + graphKindLabel(edge.kind) + ' · ' + graphEvidenceLabel(edge.evidence) + '</div></div>';
  }).join('');
  const complete = data.status === 'complete';
  const stateLabel = complete ? '已完整解析' : '部分解析';
  const coverage = complete
    ? '已解析 ' + resolved + ' 条调用，共 ' + edges.length + ' 条。'
    : '显示 ' + (data.summary?.visible_symbols ?? nodes.size) + '/' + (data.summary?.total_symbols ?? nodes.size) + ' 个定义，已解析 ' + resolved + '/' + (data.summary?.total_calls ?? edges.length) + ' 条项目调用；动态调用和外部依赖也可能未显示。';
  const notice = diagnostics ? '<details class="graph-diagnostics"><summary>解析提示 (' + data.diagnostics.length + ')</summary><ul>' + diagnostics + '</ul></details>' : '';
  const scope = path
    ? '<div class="graph-scope">当前文件：<code>' + escapeText(current.value || '') + '</code>　<button type="button" data-graph-all>查看全项目</button></div>'
    : '<div class="graph-scope">全项目静态调用</div>';
  setView('preview');
  specialContent.value = '<div class="graph">' + scope + '<div class="graph-summary" data-state="' + (complete ? 'success' : 'warning') + '"><strong>' + stateLabel + '</strong><span>' + coverage + '</span></div>' + notice + (data.truncated ? '<p>结果已达到图规模上限；请打开单个文档查看该文件的实现链。</p>' : '') + (rows || '<p>未发现可解析的静态调用边。</p>') + '</div>';
}

function resetDocumentWorkspace() {
  tabs.value = [];
  current.value = null;
  sourcePath.value = null;
  sourceOnlyPath.value = null;
  view.value = 'preview';
  specialContent.value = null;
  clearMap(documents);
  clearMap(bases);
  clearMap(sources);
  clearMap(diffs);
  sourcesReady.clear();
  clearRecord(drafts);
  taskId.value = null;
  writeReady.value = false;
  writeBusy.value = false;
}

function openBindingDialog() {
  bindingSource.value = binding.value?.source_root || 'src';
  bindingDocs.value = binding.value?.docs_root || 'docs/code-reference';
  bindingMessage.value = '';
  bindingMessageState.value = '';
  bindingPreview.value = null;
  bindingPreviewError.value = '';
  bindingDialog.value?.showModal();
  void previewBinding();
}

function bindingPathChanged() {
  bindingPreview.value = null;
  bindingPreviewError.value = '';
}

async function chooseBindingDirectory(target: 'source' | 'documents') {
  if (!window.docsAgentDesktop) return;
  try {
    const currentPath = target === 'source' ? bindingSource.value : bindingDocs.value;
    const selected = await window.docsAgentDesktop.chooseBindingDirectory(currentPath);
    if (selected === null) return;
    if (target === 'source') bindingSource.value = selected;
    else bindingDocs.value = selected;
    bindingPathChanged();
    await previewBinding();
  } catch (error) {
    bindingPreviewError.value = friendlyError(error);
  }
}

async function previewBinding() {
  if (bindingPreviewBusy.value) return;
  bindingPreviewBusy.value = true;
  bindingPreviewError.value = '';
  bindingPreview.value = null;
  try {
    const preview = await post<BindingPreview>('/api/binding/preview', {
      source_root: bindingSource.value.trim(),
      docs_root: bindingDocs.value.trim(),
    });
    bindingSource.value = preview.tree.binding.source_root;
    bindingDocs.value = preview.tree.binding.docs_root;
    bindingPreview.value = preview;
  } catch (error) {
    bindingPreviewError.value = friendlyError(error);
  } finally {
    bindingPreviewBusy.value = false;
  }
}

async function saveBinding() {
  if (bindingBusy.value) return;
  if (chatBusy.value || syncing.value) {
    bindingMessage.value = '请先等待当前 Agent 或同步任务完成。';
    bindingMessageState.value = 'error';
    return;
  }
  bindingBusy.value = true;
  bindingMessage.value = '正在验证并应用目录…';
  bindingMessageState.value = 'running';
  try {
    if (!bindingMatchesPreview.value) await previewBinding();
    if (bindingPreviewError.value || !bindingPreview.value) {
      throw new Error(bindingPreviewError.value || '请先预览目录绑定');
    }
    if (!bindingPreview.value.can_apply) {
      throw new Error('请等待当前 Agent 或同步任务完成后再改绑。');
    }
    const previewedTree = bindingPreview.value.tree;
    const unchanged = previewedTree.binding.source_root === binding.value?.source_root
      && previewedTree.binding.docs_root === binding.value?.docs_root;
    if (unchanged) {
      bindingDialog.value?.close();
      return;
    }
    const pending = bindingPreview.value.pending;
    const localEdits = proposalEditCount.value;
    const needsConfirmation = pending.has_pending || localEdits > 0;
    if (needsConfirmation) {
      const affected = [
        pending.sync_files.length ? `同步草稿：${pending.sync_files.length} 个文件` : '',
        pending.proposal_paths.length ? `待审提案：${pending.proposal_paths.length} 份` : '',
        pending.message_count ? '当前进程内的聊天记录' : '',
        localEdits ? `尚未保存的提案编辑：${localEdits} 份` : '',
      ].filter(Boolean).join('\n');
      const confirmed = await requestAction({
        title: '确认更改目录绑定',
        message: `应用后会清除当前进程中的会话和未写入工作：\n\n${affected}\n\n已写入磁盘的文件不会删除。`,
        confirmText: '丢弃并改绑',
        destructive: true,
      });
      if (!confirmed) {
        bindingMessage.value = '';
        bindingMessageState.value = '';
        return;
      }
    }
    const data = await post<TreeResponse>('/api/binding', {
      source_root: bindingSource.value.trim(),
      docs_root: bindingDocs.value.trim(),
      discard_pending: needsConfirmation,
    });
    bindingDialog.value?.close();
    resetDocumentWorkspace();
    tree.value = data;
    messages.value = [];
    proposals.value = [];
    clearRecord(proposalEdits);
    proposalDialog.value?.close();
    reviewOpen.value = false;
    execution.current = '任务状态：空闲';
    setStatus('源码目录绑定已更新');
    await Promise.all([loadGit(), loadSessions(), loadWorkspacePending(), loadAgentStatus()]);
    await loadChatHistory();
    await refreshProposals();
  } catch (error) {
    bindingMessage.value = friendlyError(error);
    bindingMessageState.value = 'error';
  } finally {
    bindingBusy.value = false;
  }
}

function openDialog(dialog: HTMLDialogElement | null) {
  if (dialog && !dialog.open) dialog.showModal();
}

async function openSettings() {
  const [data, mcp] = await Promise.all([
    request<AgentSettings>('/api/settings'),
    request<{ servers: Array<{ name: string; transport: string; command: string[]; url: string; enabled: boolean }> }>('/api/mcp'),
  ]);
  settings.model_url = data.model_url || '';
  settings.model = data.model || '';
  settings.request_format = data.request_format;
  settings.endpoint = data.endpoint || '';
  settings.api_key = '';
  settings.clear_api_key = false;
  settings.temperature = data.temperature ?? 0.2;
  settings.max_tokens = data.max_tokens ?? 2048;
  settings.instructions = data.instructions || '';
  settings.allow_source_upload = Boolean(data.allow_source_upload);
  settings.validation_commands = data.validation_commands || [];
  memoryEnabled.value = Boolean(data.memory_enabled);
  validationCommandsText.value = JSON.stringify(settings.validation_commands, null, 2);
  mcpServersText.value = JSON.stringify(mcp.servers || [], null, 2);
  const keyRequired = data.request_format !== 'ollama_chat';
  settingsMessage.value = !data.configured
    ? '尚未配置模型服务，仍可使用本地解析功能。'
    : keyRequired && !data.api_key_set
      ? '模型地址和模型已配置（' + data.request_format + '），但没有可用的 API Key。'
      : '当前 Agent 已配置（' + data.request_format + '）。' + (data.api_key_set ? ' API Key 已保存在本机用户配置中。' : '');
  settingsMessageState.value = keyRequired && data.configured && !data.api_key_set ? 'error' : '';
  openDialog(settingsDialog.value);
}

function settingsPayload(): SettingsPayload {
  let commands: unknown;
  try {
    commands = JSON.parse(validationCommandsText.value);
  } catch {
    throw new Error('验证命令必须是有效的 JSON 数组');
  }
  if (!Array.isArray(commands) || commands.some((command) => !Array.isArray(command) || !command.length || command.some((part) => typeof part !== 'string' || !part.trim()))) {
    throw new Error('验证命令格式必须为字符串参数数组的数组');
  }
  return {
    model_url: settings.model_url.trim(),
    model: settings.model.trim(),
    request_format: settings.request_format,
    endpoint: settings.endpoint.trim(),
    api_key: settings.api_key,
    clear_api_key: settings.clear_api_key,
    temperature: Number.isFinite(settings.temperature) ? settings.temperature : 0.2,
    max_tokens: Number(settings.max_tokens || 2048),
    instructions: settings.instructions.trim(),
    allow_source_upload: settings.allow_source_upload,
    validation_commands: commands as string[][],
    memory_enabled: memoryEnabled.value,
  };
}

async function testSettings() {
  settingsBusy.value = true;
  settingsMessage.value = '正在测试连接…';
  settingsMessageState.value = 'running';
  try {
    const payload = settingsPayload();
    payload.allow_source_upload = false;
    const data = await post<{ reply: string }>('/api/settings/test', { settings: payload });
    settingsMessage.value = '连接成功：' + data.reply + '（未保存设置）';
    settingsMessageState.value = 'success';
  } catch (error) {
    settingsMessage.value = '连接失败：' + friendlyError(error);
    settingsMessageState.value = 'error';
  } finally {
    settingsBusy.value = false;
  }
}

async function saveSettings() {
  settingsBusy.value = true;
  try {
    const settingsBody = settingsPayload();
    let mcpServers: unknown;
    try {
      mcpServers = JSON.parse(mcpServersText.value);
    } catch {
      throw new Error('MCP 配置必须是有效的 JSON 数组');
    }
    if (!Array.isArray(mcpServers) || mcpServers.some((item) => !item || typeof item !== 'object' || typeof (item as { name?: unknown }).name !== 'string')) {
      throw new Error('MCP 配置必须是带 name 字段的对象数组');
    }
    const data = await post<AgentSettings>('/api/settings', settingsBody);
    await post('/api/mcp', { action: 'replace', servers: mcpServers });
    settingsMessage.value = '设置已保存。' + (data.api_key_set ? 'API Key 已保存到本机用户配置，重启后仍可使用。' : '当前未保存 API Key。');
    settingsMessageState.value = 'success';
    await loadSessions();
    await reloadSessionWorkspace();
    await loadAgentStatus();
    window.setTimeout(() => settingsDialog.value?.close(), 900);
  } catch (error) {
    settingsMessage.value = friendlyError(error);
    settingsMessageState.value = 'error';
  } finally {
    settingsBusy.value = false;
  }
}

async function createSession() {
  if (chatBusy.value || sessionBusy.value) return;
  sessionBusy.value = true;
  try {
    const data = await post<{ session: Session }>('/api/chat/sessions', { action: 'create' });
    sessionId.value = data.session.id;
    persistSessionId();
    await loadSessions();
    await reloadSessionWorkspace();
    setStatus('已创建会话：' + data.session.title);
  } catch (error) {
    showOperationError('新建会话失败', error);
  } finally {
    sessionBusy.value = false;
  }
}

async function renameCurrentSession() {
  const currentSession = selectedSession();
  if (!currentSession || chatBusy.value || sessionBusy.value) return;
  const title = await requestAction({
    title: '重命名会话',
    message: '修改“' + currentSession.title + '”的显示名称。',
    confirmText: '保存',
    inputLabel: '会话名称',
    inputValue: currentSession.title,
  });
  if (typeof title !== 'string' || !title) return;
  sessionBusy.value = true;
  try {
    await post('/api/chat/sessions', { action: 'rename', session_id: currentSession.id, title });
    await loadSessions();
    setStatus('会话名称已更新');
  } catch (error) {
    showOperationError('重命名会话失败', error);
  } finally {
    sessionBusy.value = false;
  }
}

async function deleteCurrentSession() {
  const currentSession = selectedSession();
  if (!currentSession || currentSession.id === 'default' || chatBusy.value || sessionBusy.value) return;
  const confirmed = await requestAction({
    title: '删除会话',
    message: '删除“' + currentSession.title + '”？该会话的聊天记录会被移除，已写入的项目文件不受影响。',
    confirmText: '删除',
    destructive: true,
  });
  if (!confirmed) return;
  sessionBusy.value = true;
  try {
    await post('/api/chat/sessions', { action: 'delete', session_id: currentSession.id });
    await loadSessions();
    await reloadSessionWorkspace();
    setStatus('会话已删除');
  } catch (error) {
    showOperationError('删除会话失败', error);
  } finally {
    sessionBusy.value = false;
  }
}

async function clearChatSession() {
  if (chatBusy.value || sessionBusy.value || !selectedSession()) return;
  sessionBusy.value = true;
  try {
    await post('/api/chat/clear', { session_id: sessionId.value });
    messages.value = [];
    resetExecution();
    await loadSessions();
    await refreshProposals();
    setStatus('已清空当前会话消息');
  } catch (error) {
    showOperationError('清空会话失败', error);
  } finally {
    sessionBusy.value = false;
  }
}

function selectedProposalContent(): string {
  const proposal = selectedProposal.value;
  return proposal ? proposalEdits[proposal.proposal_id] ?? proposal.content : '';
}

watch(selectedProposalId, () => {
  proposalContent.value = selectedProposalContent();
  proposalValidation.value = '';
  proposalValidationState.value = '';
  proposalCanAccept.value = true;
  proposalDiffHtml.value = selectedProposal.value ? renderDiff(selectedProposal.value.diff || '当前内容与磁盘版本相同') : '';
  if (selectedProposal.value && proposalContent.value !== selectedProposal.value.content) queueProposalPreview();
});

function queueProposalPreview() {
  if (proposalPreviewTimer) clearTimeout(proposalPreviewTimer);
  const proposal = selectedProposal.value;
  if (!proposal) return;
  const content = proposalContent.value;
  proposalEdits[proposal.proposal_id] = content;
  proposalValidation.value = '正在检查 Markdown…';
  proposalValidationState.value = 'running';
  proposalCanAccept.value = false;
  const version = ++proposalPreviewVersion;
  proposalPreviewTimer = window.setTimeout(() => void previewProposal(proposal.proposal_id, content, version), 250);
}

async function previewProposal(proposalId: string, content: string, version: number) {
  const proposal = proposals.value.find((item) => item.proposal_id === proposalId);
  try {
    const data = await post<{ errors: string[]; diff: string }>('/api/chat/proposal', {
      action: 'preview',
      proposal_id: proposalId,
      session_id: proposal?.session_id || sessionId.value,
      content,
    });
    if (version !== proposalPreviewVersion || proposalId !== selectedProposalId.value) return;
    const errors = data.errors || [];
    proposalValidation.value = errors.length ? errors.join('；') : 'Markdown 结构校验通过';
    proposalValidationState.value = errors.length ? 'error' : 'success';
    proposalDiffHtml.value = renderDiff(data.diff || '当前内容与磁盘版本相同');
    proposalCanAccept.value = errors.length === 0 && !proposalBusy.value;
  } catch (error) {
    if (version !== proposalPreviewVersion) return;
    proposalValidation.value = '无法预览差异：' + friendlyError(error);
    proposalValidationState.value = 'error';
    proposalCanAccept.value = false;
  }
}

async function openProposalReview(targetSessionId?: string) {
  await refreshProposals();
  const next = proposals.value.find((item) => !targetSessionId || item.session_id === targetSessionId);
  selectedProposalId.value = next?.proposal_id || null;
  openDialog(proposalDialog.value);
}

function showOverview() {
  workspaceMode.value = 'overview';
  reviewOpen.value = false;
}

function openReviewCenter() {
  reviewOpen.value = true;
  void Promise.all([refresh(), loadSessions(), refreshProposals()]).catch((error) => showOperationError('无法加载待审与提交状态', error));
}

async function enterWorkbench() {
  workspaceMode.value = 'workbench';
  await nextTick();
}

async function startAsk() {
  workspaceMode.value = 'workbench';
  chatWorkflow.value = null;
  await nextTick();
  chatPanel.value?.focusComposer();
}

async function startDocumentTask() {
  workspaceMode.value = 'workbench';
  chatWorkflow.value = 'document';
  await nextTick();
  chatPanel.value?.focusComposer();
}

async function startFeaturePlan(input: FeaturePlanRequest, goal: string) {
  if (chatBusy.value || syncing.value) {
    setStatus('当前任务完成后才能开始功能计划。', 'warning');
    return;
  }
  featurePlanOpen.value = false;
  workspaceMode.value = 'workbench';
  await nextTick();
  await sendChat(goal, 'feature_plan', input);
}

async function handleLeaveRequest(request: { id: string; reason: 'quit' | 'project-switch' }) {
  if (!hasPendingLeave.value) {
    window.docsAgentDesktop?.respondToLeave(request.id, true);
    return;
  }
  const action = request.reason === 'project-switch' ? '切换项目' : '退出 DocsAgent';
  const details = pendingLeaveItems.value.length ? pendingLeaveItems.value.join('\n') : '当前进程内的工作状态';
  const confirmed = await requestAction({
    title: `确认${action}`,
    message: `${details}\n\n未写入的草稿和待审提案会被丢弃。已经写入磁盘的文件会保留；聊天记录会在服务停止后清除。`,
    confirmText: '丢弃并继续',
    destructive: true,
  });
  window.docsAgentDesktop?.respondToLeave(request.id, Boolean(confirmed));
}

function preventBrowserDataLoss(event: BeforeUnloadEvent) {
  if (window.docsAgentDesktop || !hasPendingLeave.value) return;
  event.preventDefault();
  event.returnValue = '';
}

async function decideProposal(actionType: 'accept' | 'reject') {
  const proposal = selectedProposal.value;
  if (!proposal || proposalBusy.value) return;
  if (actionType === 'reject') {
    const confirmed = await requestAction({
      title: '拒绝提案',
      message: '拒绝 ' + proposal.path + '？\n提案将被丢弃。',
      confirmText: '拒绝提案',
      destructive: true,
    });
    if (!confirmed) return;
  }
  proposalBusy.value = true;
  proposalCanAccept.value = false;
  try {
    const body: Record<string, unknown> = {
      action: actionType,
      proposal_id: proposal.proposal_id,
      session_id: proposal.session_id || sessionId.value,
    };
    if (actionType === 'accept') body.content = proposalContent.value;
    const data = await post<{ written?: string }>('/api/chat/proposal', body);
    delete proposalEdits[proposal.proposal_id];
    proposals.value = proposals.value.filter((item) => item.proposal_id !== proposal.proposal_id);
    selectedProposalId.value = proposals.value[0]?.proposal_id || null;
    if (!proposals.value.length) proposalDialog.value?.close();
    setStatus(actionType === 'accept' ? '已写入 ' + (data.written || proposal.path) : '已拒绝 ' + proposal.path);
    try {
      if (actionType === 'accept') await refresh();
      await refreshProposals();
    } catch (error) {
      showOperationError('操作已完成，但工作区刷新失败', error);
    }
  } catch (error) {
    showOperationError(actionType === 'accept' ? '写入提案失败' : '拒绝提案失败', error);
  } finally {
    proposalBusy.value = false;
  }
}

function setSyncing(value: boolean) {
  syncing.value = value;
  if (value) {
    writeReady.value = false;
    resetExecution('正在启动同步…');
    setExecutionState('running', '同步中', '准备扫描', '正在启动同步任务…');
    executionOpen.value = true;
  }
}

async function startSync() {
  if (syncing.value) return;
  if (chatBusy.value) {
    setStatus('Agent 正在执行当前请求，请等待完成后再同步文档。', 'warning');
    return;
  }
  setSyncing(true);
  taskId.value = null;
  clearMap(documents);
  clearMap(bases);
  clearRecord(drafts);
  clearMap(sources);
  sourcesReady.clear();
  clearMap(diffs);
  try {
    const task = await post<{ task_id: string }>('/api/sync', {});
    taskId.value = task.task_id;
    execution.current = '任务状态：生成中';
    const stream = new EventSource('/api/events?id=' + encodeURIComponent(task.task_id));
    syncStream.value = stream;
    stream.onmessage = (message) => {
      let data: SyncEvent;
      try {
        data = JSON.parse(message.data) as SyncEvent;
      } catch (error) {
        showOperationError('无法读取同步事件', error);
        return;
      }
      if (data.event === 'status') {
        const notice = data.warnings?.length ? ' · ' + data.warnings[0] : data.errors?.length ? ' · ' + data.errors[0] : '';
        setStatus((data.message || data.status || '') + notice, data.errors?.length ? 'error' : data.warnings?.length ? 'warning' : '');
        execution.current = '任务状态：' + data.status + (data.file ? ' · ' + data.file : '');
        const stateName: StatusKind = data.status === 'error' ? 'error' : data.status === 'ready' ? 'success' : 'running';
        setExecutionState(stateName, stateName === 'error' ? '同步失败' : stateName === 'success' ? '同步完成' : '同步中', data.file || '文档同步', data.message || execution.current);
        if (data.file && ['file_started', 'file_ready', 'file_error'].includes(data.status || '')) {
          const label = data.status === 'file_started' ? '开始' : data.status === 'file_ready' ? '完成' : '失败';
          addExecutionStep('sync-' + data.file, label + '：' + data.file, data.errors?.[0] || '', data.status === 'file_error' ? 'error' : data.status === 'file_ready' ? 'success' : 'running');
        }
        if (data.status === 'file_started' && data.file && !documents.has(data.file)) {
          documents.set(data.file, '');
          bases.set(data.file, '');
          drafts[data.file] = 'generating';
          if (!current.value || !drafts[current.value]) void openTab(data.file).catch((error) => showOperationError('无法打开生成文档', error));
        }
        if (data.file && data.status === 'file_ready') drafts[data.file] = 'draft';
        if (data.file && data.status === 'file_error') drafts[data.file] = 'error';
        if (data.status === 'ready') writeReady.value = true;
        if (data.status === 'error' || data.status === 'cancelled') writeReady.value = false;
        if (data.diff && data.file) diffs.set(data.file, data.diff);
        if (follow.value && data.file && data.status === 'file_started') void openTab(data.file).catch((error) => showOperationError('无法打开生成文档', error));
      } else if (data.event === 'draft' && data.file) {
        if (data.base_content !== undefined) bases.set(data.file, data.base_content || '');
        documents.set(data.file, (documents.get(data.file) || '') + (data.content || ''));
        if (follow.value || current.value === data.file) {
          void openTab(data.file).then(() => { setView('diff'); }).catch((error) => showOperationError('无法打开生成文档', error));
        }
      } else if (data.event === 'done') {
        stream.close();
        syncStream.value = null;
        writeReady.value = data.status === 'ready';
        setSyncing(false);
        setExecutionState(data.status === 'ready' ? 'success' : 'error', data.status === 'ready' ? '同步完成' : '同步未完成', '文档同步', '任务状态：' + data.status);
        void refresh().catch((error) => showOperationError('刷新项目失败', error));
      }
    };
    stream.onerror = () => {
      if (stream.readyState === EventSource.CLOSED) {
        setSyncing(false);
        showOperationError('同步事件流中断', new Error('任务可能仍在后台运行，请刷新状态后重试'));
      } else {
        setStatus('同步事件流暂时中断，正在重连…', 'warning');
      }
    };
  } catch (error) {
    setSyncing(false);
    throw error;
  }
}

async function cancelSync() {
  if (!taskId.value || !syncing.value) return;
  await post('/api/sync/cancel', { task_id: taskId.value });
  setStatus('正在取消同步…');
}

async function writeDocs() {
  if (!taskId.value || !writeReady.value || writeBusy.value) return;
  const confirmed = await requestAction({
    title: '写入文档',
    message: '确认写入本次生成的 Markdown 文档？',
    confirmText: '写入',
  });
  if (!confirmed) return;
  writeBusy.value = true;
  try {
    const data = await post<{ written: string[] }>('/api/write', { task_id: taskId.value });
    writeReady.value = false;
    clearRecord(drafts);
    setStatus('已写入 ' + data.written.length + ' 个文档');
    await refresh();
  } finally {
    writeBusy.value = false;
  }
}

async function startChatRun(message: string, pendingId: number, workflow?: 'document' | 'feature_plan', featurePlan?: FeaturePlanRequest) {
  const task = await post<{ task_id: string }>('/api/chat/start', {
    message,
    session_id: sessionId.value,
    context_path: current.value,
    ...(workflow ? { workflow } : {}),
    ...(featurePlan ? { feature_plan: featurePlan } : {}),
  });
  chatTaskId.value = task.task_id;
  const stream = new EventSource('/api/chat/events?id=' + encodeURIComponent(task.task_id));
  chatStream.value = stream;
  stream.onmessage = (event) => {
    let data: ChatEvent;
    try {
      data = JSON.parse(event.data) as ChatEvent;
    } catch (error) {
      finishChatRun(pendingId, 'error', friendlyError(error));
      return;
    }
    if (data.event === 'done') {
      const result = data.status === 'completed' ? 'completed' : data.status === 'cancelled' ? 'cancelled' : 'error';
      finishChatRun(pendingId, result, data.status === 'completed' ? data.answer || '' : data.error || '');
      return;
    }
    handleExecutionEvent(data);
    if (data.event === 'approval_required' && data.approval) {
      void resolveAgentApproval(task.task_id, data.approval).catch((error) => {
        showOperationError('命令审批失败', error);
        void post('/api/chat/cancel', { task_id: task.task_id }).catch(() => undefined);
      });
    }
    if (data.event === 'status' && data.phase === 'starting') updatePending(pendingId, data.message || '正在准备 Agent…');
    if (data.event === 'tool_started') updatePending(pendingId, '正在' + executionToolLabel(data.tool) + '…');
  };
  stream.onerror = () => {
    if (stream.readyState === EventSource.CLOSED && chatBusy.value) {
      finishChatRun(pendingId, 'error', '执行事件流已断开，请查看执行记录后重试');
    } else if (chatBusy.value) {
      setExecutionState('running', '执行中', execution.meta, '执行事件流暂时中断，正在重连…');
    }
  };
}

async function resolveAgentApproval(taskId: string, approval: NonNullable<ChatEvent['approval']>) {
  const approved = await requestAction({
    title: '确认运行命令',
    message: approval.target + '\n\n命令会以当前用户权限运行。',
    confirmText: '运行一次',
  });
  await post('/api/chat/approval', {
    task_id: taskId,
    approval_id: approval.approval_id,
    approved: approved === true,
  });
}

async function cancelChatTask() {
  if (!chatTaskId.value) return;
  await post('/api/chat/cancel', { task_id: chatTaskId.value });
  setExecutionState('warning', '正在取消', execution.meta, '等待当前操作结束…');
}

function updatePending(id: number, content: string) {
  const item = messages.value.find((message) => message.id === id);
  if (item) item.content = content;
}

function finishChatRun(pendingId: number, result: 'completed' | 'cancelled' | 'error', payload: string) {
  chatStream.value?.close();
  chatStream.value = null;
  messages.value = messages.value.filter((item) => item.id !== pendingId);
  if (result === 'completed') {
    messages.value.push({ id: ++messageSequence, role: 'agent', content: payload || 'Agent 未返回内容。' });
    setExecutionState('success', '已完成', 'Agent', '回答已返回');
    addExecutionStep('chat-terminal', 'Agent 已返回回答', '', 'success');
    setStatus('Agent 已完成当前请求');
  } else if (result === 'cancelled') {
    messages.value.push({ id: ++messageSequence, role: 'agent', content: payload || '任务已取消。' });
    setExecutionState('warning', '已取消', 'Agent', '任务已停止');
    addExecutionStep('chat-terminal', 'Agent 任务已取消', '', 'warning');
    setStatus('Agent 任务已取消', 'warning');
  } else {
    const error = friendlyError(new Error(payload || 'Agent 执行失败'));
    messages.value.push({ id: ++messageSequence, role: 'agent', content: '请求失败：' + error });
    setExecutionState('error', '执行失败', '错误', error);
    addExecutionStep('chat-terminal', 'Agent 执行失败', error, 'error');
    setStatus('Agent 执行失败：' + error, 'error');
  }
  chatBusy.value = false;
  chatTaskId.value = null;
  void Promise.all([refreshProposals(), loadSessions()]).catch((error) => showOperationError('刷新会话状态失败', error));
  void loadWorkspacePending().catch((error) => showOperationError('刷新待处理状态失败', error));
}

async function sendChat(message: string, workflow: 'document' | 'feature_plan' | null = chatWorkflow.value, featurePlan?: FeaturePlanRequest) {
  if (chatBusy.value) return;
  if (syncing.value) {
    setStatus('文档同步正在执行，请等待同步完成后再发送 Agent 请求。', 'warning');
    return;
  }
  messages.value.push({ id: ++messageSequence, role: 'user', content: message });
  chatBusy.value = true;
  resetExecution('正在准备 Agent…');
  setExecutionState('running', '执行中', '准备请求', '正在启动 Agent…');
  executionOpen.value = true;
  const pendingId = ++messageSequence;
  messages.value.push({ id: pendingId, role: 'agent', content: '正在准备 Agent…', pending: true });
  chatWorkflow.value = null;
  try {
    await startChatRun(message, pendingId, workflow || undefined, featurePlan);
  } catch (error) {
    finishChatRun(pendingId, 'error', friendlyError(error));
  }
}

function closeDrawers() {
  explorerOpen.value = false;
  chatOpen.value = false;
}

function toggleExplorer() {
  const next = !explorerOpen.value;
  closeDrawers();
  explorerOpen.value = next;
}

function toggleChat() {
  const next = !chatOpen.value;
  closeDrawers();
  chatOpen.value = next;
}

function onGlobalKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && (explorerOpen.value || chatOpen.value)) closeDrawers();
}

function onExplorerSelect(selection: { item: TreeItem; source: boolean }) {
  if (selection.source) {
    void openSource(selection.item.path);
    return;
  }
  closeDrawers();
  void openTab(selection.item.path).catch((error) => showOperationError('无法打开文档', error));
}

async function initializeWorkspace() {
  await loadSessions();
  await loadChatHistory();
  await refreshProposals();
}

onMounted(() => {
  document.addEventListener('keydown', onGlobalKeydown);
  window.addEventListener('beforeunload', preventBrowserDataLoss);
  leaveRequestCleanup.value = window.docsAgentDesktop?.onLeaveRequest((request) => {
    void handleLeaveRequest(request).catch(() => window.docsAgentDesktop?.respondToLeave(request.id, false));
  }) || null;
  void Promise.all([refresh(), initializeWorkspace(), loadAgentStatus(), loadWorkspacePending()])
    .catch((error) => showOperationError('加载项目工作区失败', error));
});

onBeforeUnmount(() => {
  document.removeEventListener('keydown', onGlobalKeydown);
  window.removeEventListener('beforeunload', preventBrowserDataLoss);
  leaveRequestCleanup.value?.();
  chatStream.value?.close();
  syncStream.value?.close();
  if (proposalPreviewTimer) clearTimeout(proposalPreviewTimer);
});
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="app-identity">
        <strong class="brand">DocsAgent</strong>
        <button type="button" class="project-name" @click="showOverview">{{ tree?.project_name || '项目工作区' }} <span>概览</span></button>
      </div>
      <div class="binding-toolbar">
        <span class="binding-label">{{ binding ? binding.source_root + ' → ' + binding.docs_root : '加载项目...' }}</span>
        <button type="button" class="quiet-action" @click="openBindingDialog">绑定目录</button>
      </div>
      <div class="topbar-actions">
        <button type="button" @click="openSettings().catch((error) => showOperationError('打开 Agent 设置失败', error))">Agent 设置</button>
        <button type="button" @click="openReviewCenter">审核与提交 <span v-if="pendingLeaveItems.length" class="count-badge">{{ pendingLeaveItems.length }}</span></button>
        <button type="button" class="primary" :disabled="syncing" @click="startSync().catch((error) => showOperationError('同步失败', error))">{{ syncing ? '同步中…' : '同步文档' }}</button>
        <details class="topbar-more">
          <summary>更多</summary>
          <div class="more-menu" @click="($event.target as HTMLElement).closest('button') && ($event.currentTarget as HTMLElement).parentElement?.removeAttribute('open')">
            <button v-if="syncing" type="button" @click="cancelSync().catch((error) => showOperationError('取消同步失败', error))">取消同步</button>
            <span v-if="git && !git.is_repo" class="git-guide">先在项目根目录执行 <code>git init</code></span>
            <button type="button" :disabled="!git?.is_repo" title="请先在项目根目录执行 git init" @click="showGitDiff">查看 Diff</button>
            <button type="button" :disabled="!git?.is_repo" title="请先在项目根目录执行 git init" @click="showGitHistory">查看历史</button>
          </div>
        </details>
      </div>
      <div class="topbar-status">
        <span class="status-dot" :data-state="git?.is_repo ? 'success' : git ? 'warning' : 'error'">{{ gitStatusText }}</span>
        <button class="mobile-only" type="button" :aria-expanded="explorerOpen" aria-controls="explorer" @click="toggleExplorer">目录</button>
        <button class="mobile-only" type="button" :aria-expanded="chatOpen" aria-controls="chatPanel" @click="toggleChat">Agent</button>
      </div>
    </header>
    <ProjectOverview
      v-if="workspaceMode === 'overview'"
      :tree="tree"
      :git="git"
      :markdown="markdownStatus"
      :settings="agentStatus"
      :pending="pendingWork"
      :loading="!tree"
      @enter="enterWorkbench"
      @ask="startAsk"
      @sync="enterWorkbench().then(() => startSync()).catch((error) => showOperationError('同步失败', error))"
      @document="startDocumentTask"
      @plan="featurePlanOpen = true"
      @review="openReviewCenter"
      @binding="openBindingDialog"
      @settings="openSettings().catch((error) => showOperationError('打开 Agent 设置失败', error))"
    />
    <main v-else class="workspace">
      <ProjectExplorer :tree="tree" :drafts="drafts" @select="onExplorerSelect" :class="{ open: explorerOpen }" />
      <EditorPane
        :tabs="tabs"
        :current="displayPath"
        :view="view"
        :content-html="contentHtml"
        :follow="follow"
        :source-available="sourceAvailable"
        :source-title="sourceTitle"
        :proposal-count="proposalCount"
        :status="status"
        :status-state="statusState"
        @open-tab="openTab"
        @view="changeView"
        @source="current && binding && openSource(sourcePathFor(current) || '')"
        @graph="showGraph().catch((error) => showOperationError('读取实现链路失败', error))"
        @proposals="openProposalReview().catch((error) => showOperationError('无法加载待审提案', error))"
        @follow="follow = !follow"
        @content-click="onContentClick"
      />
      <ChatPanel
        ref="chatPanel"
        :session-id="sessionId"
        :sessions="sessions"
        :messages="messages"
        :execution="execution"
        :execution-open="executionOpen"
        :busy="chatBusy"
        :session-busy="sessionBusy"
        :syncing="syncing"
        :workflow="chatWorkflow"
        :class="{ open: chatOpen }"
        @send="sendChat"
        @session-change="switchSession"
        @new-session="createSession"
        @rename-session="renameCurrentSession"
        @clear-chat="clearChatSession"
        @delete-session="deleteCurrentSession"
        @execution-open="executionOpen = $event"
        @cancel-task="cancelChatTask().catch((error) => showOperationError('取消 Agent 任务失败', error))"
      />
    </main>
    <button v-if="workspaceMode === 'workbench'" class="drawer-backdrop" type="button" aria-label="关闭侧栏" :hidden="!explorerOpen && !chatOpen" @click="closeDrawers" />

    <ReviewCenter
      v-if="reviewOpen"
      ref="reviewCenter"
      :sync-drafts="drafts"
      :write-ready="writeReady && !writeBusy"
      :proposals="proposals"
      :sessions="sessions"
      :session-id="sessionId"
      :git="git"
      @close="reviewOpen = false"
      @refresh="refresh().catch((error) => showOperationError('刷新项目状态失败', error))"
      @write-sync-drafts="writeDocs().catch((error) => showOperationError('写入文档失败', error))"
      @open-proposal="openProposalReview($event).catch((error) => showOperationError('无法加载待审提案', error))"
      @commit-markdown="commitDocs"
      @commit-feature="commitFeaturePlan($event).catch((error) => showOperationError('提交功能计划失败', error))"
    />
    <FeaturePlanDialog
      v-if="featurePlanOpen"
      :open="featurePlanOpen"
      :git="git"
      :settings="agentStatus"
      :tree="tree"
      :busy="chatBusy || syncing"
      @close="featurePlanOpen = false"
      @settings="openSettings().catch((error) => showOperationError('打开 Agent 设置失败', error))"
      @submit="startFeaturePlan"
    />

    <dialog ref="bindingDialog" class="settings-dialog" aria-labelledby="bindingTitle" @cancel="bindingBusy && $event.preventDefault()">
      <form @submit.prevent="saveBinding">
        <div class="settings-header">
          <div><p class="eyebrow">PROJECT PATHS</p><h2 id="bindingTitle">绑定目录</h2></div>
          <button class="icon-close" type="button" aria-label="关闭目录设置" :disabled="bindingBusy" @click="bindingDialog?.close()">×</button>
        </div>
        <div class="binding-field">
          <label for="sourceRoot">源码目录</label>
          <div class="binding-input-row"><input id="sourceRoot" v-model="bindingSource" type="text" required maxlength="512" autocomplete="off" spellcheck="false" aria-describedby="bindingPathHint" :disabled="bindingBusy || bindingPreviewBusy" @input="bindingPathChanged"><button v-if="desktopAvailable" type="button" :disabled="bindingBusy || bindingPreviewBusy" @click="chooseBindingDirectory('source')">选择目录</button></div>
        </div>
        <div class="binding-field">
          <label for="docsRoot">文档目录</label>
          <div class="binding-input-row"><input id="docsRoot" v-model="bindingDocs" type="text" required maxlength="512" autocomplete="off" spellcheck="false" aria-describedby="bindingPathHint" :disabled="bindingBusy || bindingPreviewBusy" @input="bindingPathChanged"><button v-if="desktopAvailable" type="button" :disabled="bindingBusy || bindingPreviewBusy" @click="chooseBindingDirectory('documents')">选择目录</button></div>
        </div>
        <p id="bindingPathHint" class="field-note binding-path-hint">相对于项目根目录。路径须位于项目内，两个目录不能重叠。</p>
        <button type="button" class="preview-binding-button" :disabled="bindingPreviewBusy || bindingBusy" @click="previewBinding">{{ bindingPreviewBusy ? '正在检查…' : '预览目录范围' }}</button>
        <div v-if="bindingPreview" class="binding-preview">
          <strong>{{ bindingPreview.tree.project_name }} · {{ bindingPreview.tree.sources.length }} 个源码 · {{ bindingPreview.tree.documents.length }} 份文档</strong>
          <span>{{ bindingPreview.tree.sources.filter((item) => item.status === 'missing').length }} 份待生成 · {{ bindingPreview.tree.documents.filter((item) => item.status === 'orphan').length }} 份孤立文档</span>
          <span v-if="bindingPreview.worktree_entries.length">{{ bindingPreview.worktree_entries.length }} 项未提交工作区变更；改绑不会删除已写入文件。</span>
          <span v-if="bindingPreview.pending.has_pending">待丢弃：{{ bindingPreview.pending.sync_files.length }} 个同步草稿、{{ bindingPreview.pending.proposal_paths.length }} 份提案、{{ bindingPreview.pending.message_count }} 条会话消息。</span>
          <span v-if="!bindingPreview.can_apply" class="error-text">活动任务完成后才能应用绑定。</span>
        </div>
        <div class="settings-message" role="status" aria-live="polite" :data-state="bindingMessageState">{{ bindingPreviewError || bindingMessage }}</div>
        <div class="settings-actions">
          <span class="settings-spacer" />
          <button type="button" :disabled="bindingBusy" @click="bindingDialog?.close()">取消</button>
          <button type="submit" class="primary" :disabled="bindingBusy || bindingPreviewBusy || !bindingMatchesPreview || !bindingPreview?.can_apply">应用绑定</button>
        </div>
      </form>
    </dialog>

    <dialog ref="actionDialog" class="settings-dialog action-dialog" aria-labelledby="actionTitle" @cancel.prevent="finishAction(false)" @click.self="finishAction(false)">
      <form @submit.prevent="finishAction(true)">
        <div class="settings-header">
          <div><p class="eyebrow">PROJECT ACTION</p><h2 id="actionTitle">{{ action.title }}</h2></div>
          <button class="icon-close" type="button" aria-label="关闭操作确认" @click="finishAction(false)">×</button>
        </div>
        <p class="action-message">{{ action.message }}</p>
        <label v-if="action.inputLabel" class="action-input-label" for="actionInput">
          <span>{{ action.inputLabel }}</span>
          <input id="actionInput" ref="actionInput" v-model="action.inputValue" type="text" maxlength="200" autocomplete="off">
        </label>
        <div class="settings-message" role="status" aria-live="polite">{{ action.error }}</div>
        <div class="settings-actions">
          <span class="settings-spacer" />
          <button type="button" @click="finishAction(false)">取消</button>
          <button type="submit" :class="['primary', { destructive: action.destructive }]">{{ action.confirmText }}</button>
        </div>
      </form>
    </dialog>

    <dialog ref="settingsDialog" class="settings-dialog" aria-labelledby="settingsTitle">
      <form @submit.prevent="saveSettings">
        <div class="settings-header">
          <div><p class="eyebrow">AGENT CONFIGURATION</p><h2 id="settingsTitle">Agent 设置</h2></div>
          <button class="icon-close" type="button" aria-label="关闭设置" @click="settingsDialog?.close()">×</button>
        </div>
        <label>兼容 API 地址<input v-model="settings.model_url" type="url" :placeholder="settingUrlPlaceholder"></label>
        <label>模型名称<input v-model="settings.model" type="text" placeholder="qwen2.5:7b"></label>
        <label>请求格式
          <select v-model="settings.request_format">
            <option value="openai_chat">OpenAI Chat Completions</option>
            <option value="openai_responses">OpenAI Responses</option>
            <option value="anthropic_messages">Anthropic Messages</option>
            <option value="ollama_chat">Ollama Chat</option>
            <option value="gemini_generate">Google Gemini GenerateContent</option>
            <option value="openai_compatible">自定义 OpenAI 兼容</option>
          </select>
        </label>
        <label>请求路径<input v-model="settings.endpoint" type="text" :placeholder="settingEndpointPlaceholder"></label>
        <label>API Key <span class="field-note">留空保留已保存的 Key。Key 按项目保存在本机用户配置目录，不写入项目文件。</span><input v-model="settings.api_key" type="password" placeholder="输入新 Key（可选）"></label>
        <label class="checkbox-row"><input v-model="settings.clear_api_key" type="checkbox">保存时清除本机已保存的 Key <span class="field-note">环境变量 DOCSAGENT_API_KEY 不受影响</span></label>
        <label>Temperature<input v-model.number="settings.temperature" type="number" min="0" max="2" step="0.1"></label>
        <label>最大输出 Token<input v-model.number="settings.max_tokens" type="number" min="1" max="100000" step="1"></label>
        <label>Agent 指令<textarea v-model="settings.instructions" rows="5" placeholder="定义 Agent 的角色、回答风格和边界" /></label>
        <label class="checkbox-row"><input v-model="settings.allow_source_upload" type="checkbox">允许 Agent 访问项目内容 <span class="field-note">开启后可搜索、读取和修改项目文件；代码修改会展示 Diff，Git 提交仍需确认</span></label>
        <label>自动允许的验证命令 <span class="field-note">JSON 参数数组的数组，例如 [["python", "-m", "unittest", "discover", "-s", "tests"]]。未列出的命令每次运行前都会询问。</span><textarea v-model="validationCommandsText" rows="4" spellcheck="false" /></label>
        <label>MCP 服务器 <span class="field-note">JSON 对象数组：name、transport（stdio 或 streamable_http）、command 或 url、enabled。每次工具调用都会确认。</span><textarea v-model="mcpServersText" rows="5" spellcheck="false" /></label>
        <label class="checkbox-row"><input v-model="memoryEnabled" type="checkbox">启用长期记忆 <span class="field-note">默认关闭；启用后只使用你明确保存的记忆，并可从本地数据管理接口删除</span></label>
        <div class="settings-message" role="status" aria-live="polite" :data-state="settingsMessageState">{{ settingsMessage }}</div>
        <div class="settings-actions">
          <button type="button" :disabled="settingsBusy" @click="testSettings">测试连接</button>
          <span class="settings-spacer" />
          <button type="button" @click="settingsDialog?.close()">取消</button>
          <button type="submit" class="primary" :disabled="settingsBusy">保存设置</button>
        </div>
      </form>
    </dialog>

    <dialog ref="proposalDialog" class="proposal-dialog" aria-labelledby="proposalTitle">
      <div class="proposal-shell">
        <header class="proposal-header">
          <div><p class="eyebrow">DOCUMENT REVIEW</p><h2 id="proposalTitle">待审文档提案</h2></div>
          <button class="icon-close" type="button" aria-label="关闭文档提案审阅" @click="proposalDialog?.close()">×</button>
        </header>
        <div class="proposal-layout">
          <nav class="proposal-list" aria-label="提案文件">
            <button
              v-for="proposal in proposals"
              :key="proposal.proposal_id"
              type="button"
              :class="{ active: proposal.proposal_id === selectedProposalId }"
              :disabled="proposalBusy"
              @click="selectedProposalId = proposal.proposal_id"
            >{{ proposal.path }}<small>{{ proposal.summary || '待审' }}</small></button>
          </nav>
          <section class="proposal-editor">
            <div class="proposal-file-heading">
              <strong>{{ selectedProposal?.path || '没有待审提案' }}</strong>
              <p>{{ selectedProposal?.summary || '' }}</p>
            </div>
            <label class="sr-only" for="proposalContent">编辑提案 Markdown</label>
            <textarea id="proposalContent" v-model="proposalContent" maxlength="250000" spellcheck="false" :disabled="!selectedProposal || proposalBusy" placeholder="选择一份提案查看或编辑" @input="queueProposalPreview" />
            <div class="proposal-validation" role="status" aria-live="polite" :data-state="proposalValidationState">{{ proposalValidation }}</div>
            <div class="proposal-diff" aria-label="相对当前磁盘版本的差异" v-html="proposalDiffHtml" />
          </section>
        </div>
        <footer class="proposal-actions">
          <span class="proposal-action-note">确认写入只处理当前文件</span>
          <button type="button" :disabled="!selectedProposal || proposalBusy" @click="decideProposal('reject')">拒绝</button>
          <button type="button" class="primary" :disabled="!selectedProposal || proposalBusy || !proposalCanAccept" @click="decideProposal('accept')">确认写入</button>
        </footer>
      </div>
    </dialog>
  </div>
</template>
