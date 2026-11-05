# DocsAgent

轻量级项目知识与文档 Agent。它把源码目录镜像成 Markdown 文档目录，支持源码结构解析、符号跳转、静态调用图、流式草稿 Diff 和文档 Git 记录。

## 快速开始

```bash
python -m docsagent self-check
./start.sh
```

脚本会在服务就绪后打开浏览器。按 Ctrl+C 停止服务并释放监听端口。可用 `DOCSAGENT_PORT` 修改端口，或用 `DOCSAGENT_PYTHON` 指定 Python 可执行文件。

## 前端开发

~~~bash
cd frontend
npm ci
npm run dev
~~~

开发时另开终端运行 Python 服务，Vite 会把 /api/ 请求代理到 127.0.0.1:8765。前端使用 Vue 3、TypeScript 和 Vite；npm run build 会刷新 docsagent/web/dist/。发布包包含构建产物，日常运行 docsagent serve 不需要 Node.js。

本地办公文档解析需要安装可选依赖：

~~~bash
python -m pip install -e '.[multimodal]'
~~~

隔离浏览器能力还需要安装 Playwright 浏览器运行时：

~~~bash
python -m pip install -e '.[browser]'
python -m playwright install chromium
~~~

音频和视频转写需要额外安装语音模型依赖；首次使用会按 `DOCSAGENT_WHISPER_MODEL`（默认 `base`）加载模型：

~~~bash
python -m pip install -e '.[audio]'
~~~

## 独立桌面版

桌面应用首次启动会弹出项目目录选择器；运行中可从 File > Open Project 切换目录。关闭窗口时会停止随应用启动的本地 Python 服务。

```bash
npm ci --prefix frontend
npm ci --prefix desktop
python3 -m venv desktop/.venv
desktop/.venv/bin/python -m pip install -r desktop/requirements-build.txt
DOCSAGENT_PYTHON=./.venv/bin/python npm run dist --prefix desktop
```

Windows PowerShell 将最后三步替换为 `py -3.11 -m venv desktop\.venv`、`desktop\.venv\Scripts\python.exe -m pip install -r desktop/requirements-build.txt` 和 `$env:DOCSAGENT_PYTHON = (Resolve-Path desktop\.venv\Scripts\python.exe).Path; npm run dist --prefix desktop`。安装包会生成在 `desktop/release/`，构建目标为当前操作系统。安装包内含 Python 后端，用户无需单独安装 Python。Linux x86_64 可运行 `desktop/release/DocsAgent-0.1.0.AppImage` 打开独立窗口。开发运行先执行 `npm run build:frontend --prefix desktop`，再用同一 `DOCSAGENT_PYTHON` 设置运行 `npm run dev --prefix desktop`；开发模式需要 Python 3.11 或更新版本。开发调试可设置 `DOCSAGENT_PROJECT_ROOT`，跳过首次目录选择器。

运行前端测试：

~~~bash
python -m unittest discover -s tests
cd frontend
npm test
npx playwright install chromium
npm run test:e2e
~~~

Playwright 默认使用 `4173` 端口；被占用时可设置 `DOCSAGENT_PLAYWRIGHT_PORT=4174`。

工作台顶部的“Agent 设置”可以配置兼容 API 地址、模型、请求格式（OpenAI Chat Completions、OpenAI Responses、Anthropic Messages、Ollama Chat、Google Gemini 或自定义 OpenAI 兼容）、请求路径、API Key、Temperature、最大输出 Token、系统指令、“允许 Agent 访问项目内容”和项目验证命令白名单。项目内容权限默认关闭；开启后模型可使用绑定范围内的文件列表、搜索、按行读取、受基线保护的文件编辑、项目检查命令和 Git 文档上下文。白名单外命令逐次等待用户确认，Agent 不会自动提交 Git。API Key 按项目保存在当前用户配置目录的独立文件中，并限制为当前用户访问；不会写入项目目录或 `.docsagent.toml`。连接测试使用临时设置，不会保存表单内容。

Agent 还提供公共网页搜索/读取、隔离 Playwright 浏览器、逐服务器启用的 MCP（`stdio` 或 Streamable HTTP）和本地附件解析工具。网页默认只读；MCP 元数据与只读标记不作为授权依据，每次 MCP 工具调用都需确认。PDF、DOCX、XLSX、PPTX 可提取文本；音频需要可选转写依赖，视频只处理音轨。当前没有图像视觉模型、PDF OCR 或视频画面分析，相关请求会明确失败，不会伪称完成。会话和任务事件保存在用户级本地数据库，长期记忆默认关闭。

协议对应官方接口：

- OpenAI Responses：`POST /responses`，`input`、`max_output_tokens`，读取 `output_text`。
- Anthropic Messages：`POST /v1/messages`，顶层 `system`、`messages`、`max_tokens`，使用 `anthropic-version`。
- Ollama Chat：`POST /api/chat`，`stream: false`，输出上限使用 `options.num_predict`。
- Google Gemini：`POST /v1beta/models/{model}:generateContent`，使用 `contents`、`systemInstruction`、`generationConfig`。

官方参考：

- <https://developers.openai.com/api/reference/resources/responses/methods/create.md>
- <https://docs.anthropic.com/en/api/messages.md>
- <https://docs.ollama.com/api/chat.md>
- <https://generativelanguage.googleapis.com/$discovery/rest?version=v1beta>

默认绑定：

```text
源码：src/
文档：docs/code-reference/
```

可以在项目根目录创建 `.docsagent.toml`：

```toml
[binding]
source_root = "src"
docs_root = "docs/code-reference"
```

## 项目工作流程

打开项目后先进入项目概览，可查看源码/文档绑定、Git 状态和待处理数量，并从这里开始浏览问答、同步代码文档、新建或修改 Markdown、制定功能计划。进入工作台后，左侧用“文档目录”和“源码”两个标签分别浏览文件；从源码打开文件时，如果存在对应 Markdown，会一并打开文档和源码。

“绑定目录”用于更换源码根目录和文档根目录。桌面端可使用系统目录选择器，浏览器端输入相对于项目根目录的路径。应用前会扫描并显示文件数量、待生成/孤立文档及未写入状态；活动任务结束后才能应用。改绑前会要求确认丢弃当前进程内的草稿、提案和会话状态，已写入磁盘的文件不会被删除。桌面端切换项目或退出时也会检查未写入工作。

“审核与提交”集中显示同步草稿、Agent 文档提案和工作区 Markdown。接受提案只写入文件；Git 提交前需要选择安全 Markdown 文件、查看完整 Diff 并确认提交信息。源码和 `docs/features/` 功能计划不会进入普通 Markdown 提交。功能计划单独审核和提交，生成前可选择比较区间的起点，终点固定为当前 `HEAD`；只有一个基线提交时可以不选区间。

## CLI

```bash
python -m docsagent run --root . --goal "搜索认证配置"
python -m docsagent run --root . --goal "解释认证流程" --input README.md --input src/auth.py
python -m docsagent run --root . --goal "同步代码文档"
python -m docsagent scan --root .
python -m docsagent sync --root .
python -m docsagent git status --root .
python -m docsagent git diff --root .
```

`sync` 只生成草稿，得到明确的 `y` 确认后才写入 Markdown。
`run --input` 会把选中的 Markdown 文件和（开启项目内容读取时）源码文件作为本轮显式上下文；`--output` 写入前会执行 Markdown 校验和原子替换。

右侧 Agent 栏提供会话管理：可以新建、切换、重命名、清空和删除会话。每个会话独立保存聊天历史和待审提案；删除会话前必须处理待审提案，默认会话只能清空不能删除。会话历史保存在用户级本地 SQLite；浏览器只保存当前会话 ID，不保存对话内容。

用户明确要求起草或修改项目文档时，Agent 会提交 Markdown 提案。中间编辑区的“待审提案”入口可查看差异、修改草稿并逐文件确认写入或拒绝；审批操作与 Git Diff/历史查看都不会作为消息插入 Agent 对话。清空聊天不会删除待审提案。未确认的提案不会写入磁盘。

明确提出新增需求或制定功能计划时，Agent 会在开启“允许 Agent 读取项目内容”后检查源码、测试、配置和 Git 基线，提出 docs/features/<feature-id>/requirements.md、architecture.md 和 plan.md 三份文档。计划包含需求到任务/测试的映射、架构证据和基线 commit；三份文档通过联检并由用户审核写入后，可在“审核与提交”中预览并确认提交当前功能计划。没有 Git 基线、工作区存在其他未提交修改、文档包不完整或基线发生变化时，计划提交会停止。已有功能目录必须在 Agent 明确选择“修改”后才能更新。

开启项目内容权限后，Agent 可按用户目标修改项目文件、运行已配置验证命令或经逐次确认运行其他命令。编辑会检查文件摘要以避免覆盖并发改动，返回 Diff；Git 提交始终由用户确认。

Web 工作台的同步任务带有事件 ID，断线后可从上次事件继续接收；同一时间只运行一个同步任务，并可通过“取消同步”停止后续文件处理。Agent 对话按会话保留最近消息。任务事件历史可通过本地 API 查询和删除；服务重启不会恢复正在执行的模型/工具循环。

## 模型服务（可选）

未配置模型时，项目扫描、代码解析、Markdown 生成、Git Diff 和静态调用图仍可用。需要自然语言模型时设置 OpenAI 兼容服务：

```bash
export DOCSAGENT_MODEL_URL=http://localhost:11434/v1
export DOCSAGENT_MODEL=qwen2.5:7b
export DOCSAGENT_API_KEY=optional
```

## 目录

前端位于 `frontend/`，使用 Vue 3、TypeScript 和 Vite 构建；Python 后端按 Agent、项目能力、模型供应商、基础设施和 Web 入口分层。运行时静态资源位于 `docsagent/web/dist/`。

```text
docsagent/
├── agent/
│   ├── application.py     # 应用协调与用户工作流
│   ├── context.py         # 项目、Git 和用户选中文件上下文
│   ├── loop.py            # 有轮数、调用数和上下文预算的工具循环
│   ├── prompts.py         # Agent 系统指令
│   ├── sessions.py        # 会话、历史和待处理反馈
│   ├── state.py           # 会话、提案和任务数据模型
│   ├── tasks.py           # 后台任务与事件生命周期
│   ├── tools/
│   │   ├── dispatcher.py  # 工具校验、权限门禁和执行路由
│   │   └── schemas.py     # Agent 可调用工具合同
│   └── workflows/
│       └── documents.py   # 文档计划、提案、审核和接受
├── project/
│   ├── binding.py         # 项目绑定、路径规则和扫描
│   ├── files.py           # 安全读取、搜索和规划文件
│   ├── models.py          # 源码事实与文档草稿模型
│   ├── parsing.py         # 源码解析
│   ├── documentation.py   # Markdown 生成、校验和写入
│   ├── feature_plans.py   # 功能计划校验
│   └── graph.py           # 静态实现关系图
├── providers/
│   ├── model_client.py    # 模型 API 协议适配
│   └── credentials.py     # 本机凭证存储
├── infrastructure/
│   └── git.py             # Git 状态、差异和受审核提交
├── web/
│   ├── server.py          # 本地 HTTP/SSE API
│   └── dist/              # Vue 工作台构建产物
├── cli.py                 # 命令行入口
└── __main__.py            # python -m docsagent 入口

tests/
├── test_agent_capabilities.py  # 项目编辑、命令执行与权限
├── test_agent_loop.py          # Agent 循环与终止条件
├── test_agent_runtime.py       # 会话、上下文和提案工作流
├── test_attachments.py         # 附件解析与边界
├── test_browser_access.py      # 隔离浏览器
├── test_cli.py                 # CLI 草稿警告
├── test_document_pipeline.py   # 项目解析与文档流水线
├── test_feature_plans.py       # 计划校验与 Git 提交
├── test_mcp_client.py          # MCP 协议客户端
├── test_mcp_manager.py         # MCP 配置与工具发现
├── test_memory.py              # 长期记忆
├── test_model_client.py        # 模型供应商协议
├── test_run_store.py           # 任务事件持久化
├── test_server_web.py          # HTTP API 与静态资源
├── test_session_persistence.py # 会话持久化
├── test_sessions.py            # 会话上下文边界
├── test_start_script.py        # 启动握手与进程回收
└── test_web_access.py          # 网页读取与 SSRF 防护
```
