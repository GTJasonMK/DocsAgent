import { expect, test, type Page, type Route } from '@playwright/test';

function sse(events: object[]): string {
  return events.map((event, index) => 'id: ' + (index + 1) + '\ndata: ' + JSON.stringify(event) + '\n\n').join('');
}

async function json(route: Route, value: unknown) {
  await route.fulfill({
    status: 200,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify(value),
  });
}

async function installApi(page: Page) {
  let proposals = [{
    proposal_id: 'proposal-1',
    path: 'docs/guide.md',
    summary: '补充指南',
    content: '# Guide\n\nDraft text.\n',
    diff: '--- disk\n+++ draft\n+Draft text.',
  }];
  const sessions = [{ id: 'default', title: '默认会话', message_count: 0, pending_proposals: 1 }];
  let binding = { source_root: 'src', docs_root: 'docs/code-reference' };
  const baseCommit = 'a'.repeat(40);
  const headCommit = 'b'.repeat(40);
  const tree = () => ({
    project_name: 'demo',
    feature_ids: ['add-workflow'],
    binding,
    documents: [
      { path: 'docs/guide.md', status: 'manual' },
      { path: 'docs/code-reference/src/main.py.md', status: 'synced' },
      { path: 'docs/features/add-workflow/requirements.md', status: 'manual' },
      { path: 'docs/features/add-workflow/architecture.md', status: 'manual' },
      { path: 'docs/features/add-workflow/plan.md', status: 'manual' },
    ],
    sources: [{ path: 'src/main.py' }],
  });

  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    if (method === 'GET' && path === '/api/tree') {
      await json(route, tree());
    } else if (method === 'GET' && path === '/api/git/status') {
      await json(route, {
        is_repo: true,
        branch: 'main',
        head_commit: headCommit,
        entries: [],
        worktree_entries: [
          'docs/features/add-workflow/requirements.md',
          'docs/features/add-workflow/architecture.md',
          'docs/features/add-workflow/plan.md',
        ].map((path) => ({ path, code: ' M' })),
      });
    } else if (method === 'GET' && path === '/api/git/markdown/status') {
      await json(route, {
        is_repo: true, branch: 'main', head_commit: headCommit, entries: [{ path: 'docs/guide.md', code: ' M' }],
        entries_truncated: false, changed_documents: 1,
      });
    } else if (method === 'GET' && path === '/api/workspace/pending') {
      await json(route, {
        active_sync: false, active_agent: false, sync_files: [], proposal_paths: [],
        message_count: 0, has_pending: false,
      });
    } else if (method === 'GET' && path === '/api/git/markdown/diff') {
      await json(route, { paths: ['docs/guide.md'], diff: '--- a/docs/guide.md\n+++ b/docs/guide.md\n+guide', digest: 'c'.repeat(64), truncated: false });
    } else if (method === 'POST' && path === '/api/git/commit') {
      await json(route, { result: 'markdown committed' });
    } else if (method === 'POST' && path === '/api/binding/preview') {
      const requested = request.postDataJSON() as { source_root: string; docs_root: string };
      await json(route, {
        tree: { ...tree(), binding: requested },
        pending: {
          active_sync: false, active_agent: false, sync_files: [], proposal_paths: [],
          message_count: 0, has_pending: false,
        },
        can_apply: true,
        worktree_entries: [],
      });
    } else if (method === 'POST' && path === '/api/binding') {
      binding = request.postDataJSON() as typeof binding;
      await json(route, tree());
    } else if (method === 'GET' && path === '/api/git/commits') {
      await json(route, {
        head_commit: headCommit,
        items: [
          { commit: headCommit, date: 'today', subject: 'implementation' },
          { commit: baseCommit, date: 'yesterday', subject: 'baseline' },
        ],
      });
    } else if (method === 'GET' && path === '/api/git/compare') {
      await json(route, { from_commit: baseCommit, to_commit: headCommit, files: ['src/main.py'], diff: '+VALUE = 2', truncated: false });
    } else if (method === 'GET' && path === '/api/document') {
      await json(route, { content: '# Guide\n\nOpen the [next page](next.md#start).\n' });
    } else if (method === 'GET' && path === '/api/source') {
      await json(route, { content: 'def main():\n    return 1\n' });
    } else if (method === 'GET' && path === '/api/chat/sessions') {
      await json(route, { sessions });
    } else if (method === 'GET' && path === '/api/chat/history') {
      await json(route, { messages: [] });
    } else if (method === 'GET' && path === '/api/chat/proposals') {
      await json(route, { proposals });
    } else if (method === 'GET' && path === '/api/settings') {
      await json(route, {
        model_url: '', model: '', request_format: 'openai_chat', endpoint: '',
        temperature: 0.2, max_tokens: 2048, instructions: '',
        allow_source_upload: true, configured: true, api_key_set: false,
      });
    } else if (method === 'GET' && path === '/api/graph') {
      await json(route, {
        status: 'complete',
        nodes: [
          { id: 'a', label: 'main', document: 'docs/guide.md', anchor: 'main' },
          { id: 'b', label: 'render', document: 'docs/guide.md', anchor: 'render' },
        ],
        edges: [{ from: 'a', to: 'b', kind: 'calls', evidence: 'static', status: 'resolved' }],
        diagnostics: [],
      });
    } else if (method === 'GET' && path === '/api/git/diff') {
      await json(route, { diff: '+guide', truncated: false });
    } else if (method === 'GET' && path === '/api/git/feature-plan/diff') {
      await json(route, {
        feature_id: 'add-workflow',
        base_commit: 'a'.repeat(40),
        baseline_matches_head: true,
        valid: true,
        errors: [],
        files: [
          'docs/features/add-workflow/requirements.md',
          'docs/features/add-workflow/architecture.md',
          'docs/features/add-workflow/plan.md',
        ],
        diff: '+feature plan',
        digest: 'd'.repeat(64),
        truncated: false,
      });
    } else if (method === 'POST' && path === '/api/git/feature-plan/commit') {
      await json(route, { feature_id: 'add-workflow', result: 'feature plan committed' });
    } else if (method === 'GET' && path === '/api/git/log') {
      await json(route, { items: [{ commit: '12345678abcd', date: 'today', subject: 'docs: update' }] });
    } else if (method === 'POST' && path === '/api/sync') {
      await json(route, { task_id: 'sync-1' });
    } else if (method === 'GET' && path === '/api/events') {
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'text/event-stream; charset=utf-8', 'cache-control': 'no-cache' },
        body: sse([
          { event: 'status', status: 'file_started', file: 'docs/generated.md', message: '开始生成' },
          { event: 'draft', file: 'docs/generated.md', base_content: '', content: '# Generated\n' },
          { event: 'status', status: 'file_ready', file: 'docs/generated.md', message: '已生成' },
          { event: 'status', status: 'ready', message: '同步完成' },
          { event: 'done', status: 'ready' },
        ]),
      });
    } else if (method === 'POST' && path === '/api/write') {
      await json(route, { written: ['docs/generated.md'] });
    } else if (method === 'POST' && path === '/api/chat/start') {
      await json(route, { task_id: 'chat-1' });
    } else if (method === 'GET' && path === '/api/chat/events') {
      await route.fulfill({
        status: 200,
        headers: { 'content-type': 'text/event-stream; charset=utf-8', 'cache-control': 'no-cache' },
        body: sse([
          { event: 'status', phase: 'starting', message: '正在准备 Agent…' },
          { event: 'done', status: 'completed', answer: '已根据项目完成回答。' },
        ]),
      });
    } else if (method === 'POST' && path === '/api/chat/proposal') {
      const body = request.postDataJSON() as { action: string };
      if (body.action === 'preview') {
        await json(route, { errors: [], diff: '--- disk\n+++ draft\n+Edited text.' });
      } else {
        proposals = [];
        await json(route, { written: 'docs/guide.md' });
      }
    } else if (method === 'POST' && path === '/api/settings/test') {
      await json(route, { reply: '连接正常' });
    } else {
      await json(route, {});
    }
  });
}

test.beforeEach(async ({ page }) => {
  await installApi(page);
});

async function enterWorkbench(page: Page) {
  await page.getByRole('button', { name: '进入工作台' }).click();
}

test('starts at project overview and previews a directory binding before applying it', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'demo' })).toBeVisible();
  await expect(page.getByRole('button', { name: /同步代码文档/ })).toBeVisible();
  await page.getByRole('button', { name: '编辑绑定' }).click();

  await page.locator('#sourceRoot').fill('packages/app');
  await page.locator('#docsRoot').fill('docs/manual');
  await page.getByRole('button', { name: '预览目录范围' }).click();
  const bindingDialog = page.getByRole('dialog', { name: '绑定目录' });
  await expect(bindingDialog.locator('.binding-preview')).toContainText('demo · 1 个源码 · 5 份文档');

  const applyRequest = page.waitForRequest('**/api/binding');
  await bindingDialog.getByRole('button', { name: '应用绑定' }).click();
  expect((await applyRequest).postDataJSON()).toMatchObject({
    source_root: 'packages/app',
    docs_root: 'docs/manual',
    discard_pending: false,
  });
  await expect(page.locator('.binding-label')).toContainText('packages/app → docs/manual');
});

test('keeps document navigation and workspace within desktop and mobile viewports', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'demo' })).toBeVisible();
  await enterWorkbench(page);
  await expect(page.locator('#explorer').getByText('文档目录', { exact: true })).toBeVisible();
  await expect(page.getByText('从左侧目录打开 Markdown，或同步源码文档开始工作。')).toBeVisible();
  await page.getByRole('button', { name: /main\.py\.md/ }).click();
  await expect(page.getByRole('heading', { name: 'Guide' })).toBeVisible();
  await page.locator('.view-switch').getByRole('tab', { name: '源码' }).click();
  await expect(page.getByText('def main():')).toBeVisible();
  await page.getByRole('button', { name: '链路' }).click();
  await expect(page.getByText('已完整解析')).toBeVisible();

  for (const width of [1440, 1024, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    const layout = await page.evaluate(() => ({
      overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      editorBottom: document.querySelector('.editor')?.getBoundingClientRect().bottom || 0,
      viewportHeight: innerHeight,
    }));
    expect(layout.overflow, 'horizontal overflow at ' + width + 'px').toBe(0);
    expect(layout.editorBottom, 'workbench should fill the viewport at ' + width + 'px').toBeGreaterThanOrEqual(layout.viewportHeight - 16);
  }

  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await enterWorkbench(page);
  await expect(page.getByText('点顶部“目录”打开 Markdown，或同步源码文档开始工作。')).toBeVisible();
  await page.getByRole('button', { name: '目录', exact: true }).click();
  await expect(page.locator('#explorer')).toHaveClass(/open/);
});

test('streams sync drafts, requires confirmation to write, and completes chat', async ({ page }) => {
  await page.goto('/');
  await enterWorkbench(page);
  await page.getByRole('button', { name: '同步文档' }).click();
  await expect(page.locator('#currentPath')).toHaveText('docs/generated.md');
  await page.getByRole('button', { name: /审核与提交/ }).click();
  const review = page.getByRole('dialog', { name: '待审与提交' });
  await review.getByRole('button', { name: '确认写入' }).click();
  const confirm = page.getByRole('dialog', { name: '写入文档' });
  await expect(confirm).toContainText('确认写入本次生成的 Markdown 文档');
  await confirm.getByRole('button', { name: '写入' }).click();
  await expect(page.getByText('已写入 1 个文档')).toBeVisible();
  await review.getByRole('button', { name: '关闭待审与提交' }).click();

  await page.getByLabel('Agent 消息').fill('解释项目入口');
  await page.getByRole('button', { name: '发送' }).click();
  await expect(page.getByText('已根据项目完成回答。')).toBeVisible();
});

test('validates proposals, tests settings without saving, and preserves zero temperature when saving', async ({ page }) => {
  await page.goto('/');
  await enterWorkbench(page);
  await page.getByRole('button', { name: /待审提案/ }).click();
  const proposal = page.getByRole('dialog', { name: '待审文档提案' });
  await proposal.locator('#proposalContent').fill('# Guide\n\nEdited text.\n');
  await expect(proposal.getByText('Markdown 结构校验通过')).toBeVisible();
  await proposal.getByRole('button', { name: '确认写入' }).click();
  await expect(page.getByText('已写入 docs/guide.md')).toBeVisible();

  await page.getByRole('button', { name: 'Agent 设置' }).click();
  await page.getByRole('button', { name: '测试连接' }).click();
  await expect(page.getByText('连接成功：连接正常（未保存设置）')).toBeVisible();

  await page.getByLabel('Temperature').fill('0');
  const saveRequest = page.waitForRequest('**/api/settings');
  await page.getByRole('button', { name: '保存设置' }).click();
  expect((await saveRequest).postDataJSON()).toMatchObject({ temperature: 0 });
});

test('reviews a full Markdown Diff and confirms a path-limited commit', async ({ page }) => {
  await page.goto('/');
  await enterWorkbench(page);
  await page.getByRole('button', { name: /审核与提交/ }).click();
  const review = page.getByRole('dialog', { name: '待审与提交' });
  await review.getByRole('button', { name: '预览所选 Diff' }).click();
  await expect(review.getByLabel('所选 Markdown 的 Git Diff')).toContainText('guide');

  const commitRequest = page.waitForRequest('**/api/git/commit');
  await review.getByRole('button', { name: '提交所选文档' }).click();
  const confirm = page.getByRole('dialog', { name: '提交文档' });
  await confirm.getByRole('button', { name: '提交', exact: true }).click();
  const request = await commitRequest;
  expect(request.postDataJSON()).toMatchObject({
    confirm: true,
    paths: ['docs/guide.md'],
    expected_diff_digest: 'c'.repeat(64),
  });
  await expect(page.locator('#status')).toContainText('markdown committed');
});

test('uses the selected ancestor-to-HEAD range for a new feature plan', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: /制定功能计划/ }).click();
  await page.getByRole('textbox', { name: '功能 ID' }).fill('add-workflow');
  await page.getByLabel('计划目标').fill('Add the new workflow to the project.');
  await page.getByLabel('功能包操作').selectOption('modify');
  await page.getByLabel('区间起点').selectOption('a'.repeat(40));
  await expect(page.locator('.range-preview')).toContainText('src/main.py');
  await expect(page.getByRole('button', { name: '生成计划草稿' })).toBeEnabled();

  const startRequest = page.waitForRequest('**/api/chat/start');
  await page.getByRole('button', { name: '生成计划草稿' }).click();
  const request = await startRequest;
  expect(request.postDataJSON()).toMatchObject({
    workflow: 'feature_plan',
    feature_plan: {
      feature_id: 'add-workflow',
      action: 'modify',
      from_commit: 'a'.repeat(40),
      to_commit: 'b'.repeat(40),
    },
  });
  await expect(page.getByText('已根据项目完成回答。')).toBeVisible();
});

test('reviews and confirms a feature-plan-only Git commit', async ({ page }) => {
  await page.goto('/');
  await enterWorkbench(page);
  await page.getByRole('button', { name: /审核与提交/ }).click();
  const review = page.getByRole('dialog', { name: '待审与提交' });
  await review.getByRole('button', { name: '审核并提交 add-workflow' }).click();
  const dialog = page.getByRole('dialog', { name: '提交功能计划' });
  await expect(dialog).toContainText('docs/features/add-workflow/requirements.md');
  await expect(dialog).toContainText('docs/features/add-workflow/architecture.md');
  await expect(dialog).toContainText('docs/features/add-workflow/plan.md');

  const commitRequest = page.waitForRequest('**/api/git/feature-plan/commit');
  await dialog.getByRole('button', { name: '提交计划' }).click();
  const request = await commitRequest;
  expect(request.postDataJSON()).toMatchObject({
    confirm: true,
    feature_id: 'add-workflow',
    message: 'docs(feature): plan add-workflow',
    expected_diff_digest: 'd'.repeat(64),
  });
  await expect(page.getByText('feature plan committed')).toBeVisible();
});

test('blocks feature-plan commit when the reviewed bundle is invalid', async ({ page }) => {
  await page.route('**/api/git/feature-plan/diff**', async (route) => {
    await json(route, {
      feature_id: 'add-workflow',
      base_commit: 'a'.repeat(40),
      baseline_matches_head: true,
      valid: false,
      errors: ['plan.md missing TEST mapping'],
      files: ['docs/features/add-workflow/plan.md'],
      diff: '+invalid plan',
      digest: 'e'.repeat(64),
      truncated: false,
    });
  });
  await page.goto('/');
  await enterWorkbench(page);
  await page.getByRole('button', { name: /审核与提交/ }).click();
  const review = page.getByRole('dialog', { name: '待审与提交' });
  await review.getByRole('button', { name: '审核并提交 add-workflow' }).click();
  await expect(page.locator('#status')).toContainText('功能文档包未通过校验');
  await expect(page.getByRole('dialog', { name: '提交功能计划' })).toHaveCount(0);
});
