export type ViewMode = 'preview' | 'source' | 'split' | 'diff';
export type StatusKind = 'idle' | 'running' | 'success' | 'warning' | 'error';
export type RequestFormat = 'openai_chat' | 'openai_responses' | 'anthropic_messages' | 'ollama_chat' | 'gemini_generate' | 'openai_compatible';

export interface Binding {
  root?: string;
  source_root: string;
  docs_root: string;
}

export interface TreeItem {
  path: string;
  status?: string;
  kind?: string;
}

export interface TreeResponse {
  project_name?: string;
  feature_ids?: string[];
  binding: Binding;
  documents: TreeItem[];
  sources: TreeItem[];
}

export interface GitEntry {
  path: string;
  code?: string;
}

export interface GitStatus {
  is_repo: boolean;
  branch?: string;
  entries: GitEntry[];
  worktree_entries?: GitEntry[];
  head_commit?: string;
  changed_documents?: number;
}

export interface MarkdownStatus {
  is_repo: boolean;
  branch: string;
  head_commit: string;
  entries: GitEntry[];
  entries_truncated: boolean;
  changed_documents: number;
}

export interface PendingWork {
  active_sync: boolean;
  active_agent: boolean;
  sync_files: string[];
  proposal_paths: string[];
  message_count: number;
  has_pending: boolean;
}

export interface FeaturePlanDiff {
  feature_id: string;
  base_commit: string;
  baseline_matches_head: boolean;
  valid: boolean;
  errors: string[];
  files: string[];
  diff: string;
  digest: string;
  truncated: boolean;
}

export interface GitLogItem {
  commit: string;
  date: string;
  subject: string;
}

export interface Session {
  id: string;
  title: string;
  message_count: number;
  pending_proposals: number;
  active?: boolean;
}

export interface ConversationMessage {
  role: string;
  content: string;
}

export interface Proposal {
  proposal_id: string;
  session_id?: string;
  path: string;
  summary: string;
  content: string;
  diff: string;
}

export interface AgentSettings {
  model_url: string;
  model: string;
  request_format: RequestFormat;
  endpoint: string;
  temperature: number;
  max_tokens: number;
  instructions: string;
  allow_source_upload: boolean;
  validation_commands: string[][];
  memory_enabled: boolean;
  configured: boolean;
  api_key_set: boolean;
}

export interface SettingsPayload extends Omit<AgentSettings, 'configured' | 'api_key_set'> {
  api_key: string;
  clear_api_key: boolean;
}

export interface ExecutionStep {
  id: string;
  label: string;
  detail: string;
  state: StatusKind;
}

export interface ExecutionState {
  state: StatusKind;
  title: string;
  meta: string;
  current: string;
  steps: ExecutionStep[];
}

export interface ChatMessage {
  id: number;
  role: 'user' | 'agent';
  content: string;
  pending?: boolean;
}

export interface ApiErrorBody {
  error?: string;
}

export interface SyncEvent {
  event: string;
  status?: string;
  message?: string;
  file?: string;
  warnings?: string[];
  errors?: string[];
  content?: string;
  base_content?: string;
  diff?: string;
}

export interface ChatEvent {
  event: string;
  phase?: string;
  status?: string;
  message?: string;
  answer?: string;
  error?: string;
  tool?: string;
  call_id?: string;
  summary?: string;
  ok?: boolean;
  round?: number;
  max_rounds?: number;
  tool_calls?: number;
  max_tool_calls?: number;
  kind?: string;
  path?: string;
  diff?: string;
  sha256?: string;
  approval?: {
    approval_id: string;
    action: string;
    summary: string;
    target: string;
    risk: string;
    status: string;
  };
}

export interface GraphNode {
  id: string;
  label: string;
  document?: string;
  anchor?: string;
}

export interface GraphEdge {
  from: string;
  to: string;
  kind: string;
  evidence: string;
  status: string;
}

export interface GraphDiagnostic {
  path: string;
  messages: string[];
}

export interface GraphResponse {
  status: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  diagnostics: GraphDiagnostic[];
  summary?: {
    visible_symbols?: number;
    total_symbols?: number;
    total_calls?: number;
  };
  truncated?: boolean;
}

export interface CommitHistory {
  head_commit: string;
  items: GitLogItem[];
}

export interface CommitRangeDiff {
  from_commit: string;
  to_commit: string;
  files: string[];
  diff: string;
  truncated: boolean;
}

export interface BindingPreview {
  tree: TreeResponse;
  pending: PendingWork;
  can_apply: boolean;
  worktree_entries: GitEntry[];
}

export interface FeaturePlanRequest {
  feature_id: string;
  action: 'create' | 'modify';
  from_commit?: string;
  to_commit?: string;
}

declare global {
  interface Window {
    docsAgentDesktop?: {
      chooseBindingDirectory(currentPath: string): Promise<string | null>;
      onLeaveRequest(listener: (request: { id: string; reason: 'quit' | 'project-switch' }) => void): () => void;
      respondToLeave(id: string, allow: boolean): void;
    };
  }
}
