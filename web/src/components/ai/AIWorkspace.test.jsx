import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import AIWorkspace from './AIWorkspace';
import { aiApi } from '../../api/aiApi';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const mockConfig = {
  platforms: [
    {
      id: 'deepseek',
      name: 'DeepSeek',
      displayName: 'DeepSeek',
      baseUrl: 'https://api.deepseek.com/v1',
      apiProtocol: 'openai_chat',
      apiKey: 'sk-test',
      enabled: true,
      isPreset: true,
      models: [
        {
          id: 'deepseek/deepseek-chat',
          modelId: 'deepseek-chat',
          displayName: 'DeepSeek Chat',
          platformId: 'deepseek',
          enabled: true,
          hasVision: false,
          enableThinking: false,
          thinkingEffort: 'medium',
          temperature: 0.2,
          maxTokens: 4096,
        },
      ],
    },
    {
      id: 'openai',
      name: 'OpenAI',
      displayName: 'OpenAI',
      baseUrl: 'https://api.openai.com/v1',
      apiProtocol: 'openai_chat',
      apiKey: '',
      enabled: true,
      isPreset: true,
      models: [
        {
          id: 'openai/gpt-4o',
          modelId: 'gpt-4o',
          displayName: 'GPT-4o',
          platformId: 'openai',
          enabled: true,
          hasVision: true,
          enableThinking: false,
          thinkingEffort: 'medium',
          temperature: 0.2,
          maxTokens: 4096,
        },
      ],
    },
  ],
  selectedTextModels: ['deepseek/deepseek-chat'],
  selectedSummaryModels: [],
  selectedVisionModel: null,
  selectedAgentModel: null,
  search: { enabled: false, provider: 'tavily', tavilyApiKey: '' },
  engine: {
    enabled: true,
    priority: 'local_ocs_ai',
    autoSaveToBank: true,
    defaultFolderId: 0,
    enableSameQuestionCheck: true,
    enableVisionForImages: true,
    modelTimeout: 45,
    modelRetryCount: 2,
    localServerEnabled: false,
    localServerPort: 3005,
    localServerToken: '',
  },
};

function setupDefaultMocks(configOverride = mockConfig) {
  vi.spyOn(aiApi, 'getConfig').mockResolvedValue({
    config: configOverride,
    searchProviders: [{ id: 'tavily', name: 'Tavily', keyField: 'tavilyApiKey' }],
    server: { running: false, port: 3005, url: 'http://127.0.0.1:3005/query', ocsConfigJson: '[]' },
  });
  vi.spyOn(aiApi, 'getStats').mockResolvedValue({
    totalQuestions: 12,
    aiQuestions: 8,
    pendingCorrections: 1,
    folderCount: 2,
    subjectCount: 1,
    knowledgeNodeCount: 5,
    practiceTotal: 20,
    todayRequests: 4,
    dailyCounts: [{ day: '2026-11-22', count: 4 }],
  });
  vi.spyOn(aiApi, 'listLogs').mockResolvedValue({ items: [] });
  vi.spyOn(aiApi, 'listFolders').mockResolvedValue({
    items: [{ id: 0, name: '默认文件夹', parentId: 0, questionCount: 12 }],
  });
  vi.spyOn(aiApi, 'listQuestions').mockResolvedValue({
    items: [
      {
        id: 1,
        question: '凸轮机构中从动件运动规律取决于？',
        options: 'A. 压力角\nB. 传动角',
        questionType: 'single',
        answer: '传动角',
        explanation: '',
        folderId: 0,
        folderName: '默认文件夹',
        isAi: true,
        isPendingCorrection: false,
        mastery: 1,
      },
    ],
    total: 1,
    page: 1,
    pageSize: 30,
  });
  vi.spyOn(aiApi, 'getPracticeQueue').mockResolvedValue({ items: [] });
  vi.spyOn(aiApi, 'getPracticeHistory').mockResolvedValue({ total: 0, correct: 0, accuracy: 0, items: [] });
  vi.spyOn(aiApi, 'listSubjects').mockResolvedValue({ items: [] });
  vi.spyOn(aiApi, 'listAgentSessions').mockResolvedValue({ items: [] });
}

describe('AIWorkspace', () => {
  it('renders dashboard and switches between all AI modules', async () => {
    setupDefaultMocks();
    const onBack = vi.fn();
    render(<AIWorkspace onBack={onBack} />);

    expect(await screen.findByText('分层答题模型调度')).toBeTruthy();
    expect(screen.getByText('在线查题测试（完整管线）')).toBeTruthy();
    // Unconfigured models are collapsed by default instead of flooding the model picker
    expect(screen.getByText(/显示未配置 Key 的平台模型 \(1\)/)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '本地题库管理' }));
    expect(await screen.findByText('凸轮机构中从动件运动规律取决于？')).toBeTruthy();
    // Displays localized Chinese question type badge instead of raw "single"
    expect(screen.getAllByText('单选题').length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: '智能刷题练习' }));
    expect(await screen.findByText(/当前筛选条件下没有可练习的题目/)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '知识图谱复习' }));
    expect(await screen.findByText('选择或新建一个学科')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'AI 助教对话' }));
    expect(await screen.findByText('AI 学习助教')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '模型与搜题设置' }));
    expect(await screen.findByText('AI 供应商、联网搜题与协同引擎设置')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /返回超星学习通/ }));
    expect(onBack).toHaveBeenCalledOnce();
  });

  it('navigates from dashboard pending KPI card directly to pending questions and requires delete confirmation', async () => {
    setupDefaultMocks();
    const listSpy = vi.spyOn(aiApi, 'listQuestions');
    const delSpy = vi.spyOn(aiApi, 'deleteQuestions').mockResolvedValue({ deleted: 1 });

    render(<AIWorkspace onBack={vi.fn()} />);
    expect(await screen.findByText('待修正错题')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /待修正错题/ }));
    await waitFor(() => {
      expect(listSpy).toHaveBeenCalledWith(expect.objectContaining({ pendingOnly: true }));
    });

    // Clicking delete icon opens confirmation modal instead of immediately deleting
    fireEvent.click(screen.getByTitle('删除题目'));
    expect(await screen.findByText('确认删除题目')).toBeTruthy();
    expect(delSpy).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '确认删除' }));
    await waitFor(() => {
      expect(delSpy).toHaveBeenCalledWith([1]);
    });
  });

  it('supports practice keyboard selection, submission feedback, and settings sub-tabs with dirty state', async () => {
    setupDefaultMocks();
    vi.spyOn(aiApi, 'getPracticeQueue').mockResolvedValue({
      items: [
        {
          id: 10,
          question: 'TCP 属于哪一层协议？',
          options: 'A. 网络层\nB. 传输层',
          questionType: 'single',
          answer: '传输层',
          explanation: 'TCP 位于传输层。',
          folderName: '默认文件夹',
          mastery: 0,
          wrongCount: 0,
        },
      ],
    });
    const submitSpy = vi.spyOn(aiApi, 'submitPractice').mockResolvedValue({
      isCorrect: true,
      correctAnswer: '传输层',
      explanation: 'TCP 位于传输层。',
    });

    render(<AIWorkspace onBack={vi.fn()} initialTab="practice" />);
    expect(await screen.findByText('TCP 属于哪一层协议？')).toBeTruthy();

    fireEvent.keyDown(window, { key: 'b' });
    fireEvent.keyDown(window, { key: 'Enter' });

    await waitFor(() => {
      expect(submitSpy).toHaveBeenCalledWith({ questionId: 10, userAnswer: '传输层' });
    });
    expect(await screen.findByText(/回答正确/)).toBeTruthy();

    // Switch to settings, verify sub-tabs and dirty/discard state
    fireEvent.click(screen.getByRole('button', { name: '模型与搜题设置' }));
    expect(await screen.findByText('AI 供应商与模型')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '双题库与协同引擎' }));
    expect(await screen.findByText('双题库协同与高级引擎策略')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /仅本地 SQLite \+ 内置 AI/ }));
    expect(await screen.findByText('有未保存修改')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '还原修改' }));
    await waitFor(() => {
      expect(screen.queryByText('有未保存修改')).toBeNull();
    });
  });

  it('keeps practice progress across tab switches, opens slide-over quick tutor, and supports in-place correction', async () => {
    setupDefaultMocks();
    vi.spyOn(aiApi, 'getPracticeQueue').mockResolvedValue({
      items: [
        {
          id: 10,
          question: 'TCP 属于哪一层协议？',
          options: 'A. 网络层\nB. 传输层',
          questionType: 'single',
          answer: '传输层',
          explanation: 'TCP 位于传输层。',
          folderName: '默认文件夹',
          mastery: 0,
          wrongCount: 0,
        },
        {
          id: 11,
          question: 'HTTP 默认端口是？',
          options: 'A. 80\nB. 443',
          questionType: 'single',
          answer: '80',
          explanation: '',
          folderName: '默认文件夹',
          mastery: 0,
          wrongCount: 0,
        },
      ],
    });
    vi.spyOn(aiApi, 'submitPractice').mockResolvedValue({
      isCorrect: true,
      correctAnswer: '传输层',
      explanation: 'TCP 位于传输层。',
    });
    const chatSpy = vi.spyOn(aiApi, 'chatWithAgent').mockResolvedValue({
      session: {
        id: 'sess-1',
        title: '快捷辅导',
        messages: [
          { role: 'user', content: 'q' },
          { role: 'assistant', content: 'HTTP 默认使用 80 端口。', modelName: 'DeepSeek' },
        ],
      },
    });
    const correctionSpy = vi.spyOn(aiApi, 'setCorrection').mockResolvedValue({ ok: true });

    render(<AIWorkspace onBack={vi.fn()} initialTab="practice" />);
    expect(await screen.findByText('TCP 属于哪一层协议？')).toBeTruthy();

    // Answer Q1 and advance to Q2
    fireEvent.keyDown(window, { key: 'b' });
    fireEvent.keyDown(window, { key: 'Enter' });
    expect(await screen.findByText(/回答正确/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /下一题/ }));
    expect(await screen.findByText('HTTP 默认端口是？')).toBeTruthy();

    // Summon slide-over Quick Tutor Drawer without leaving Practice
    fireEvent.click(screen.getByRole('button', { name: /问 AI 助教/ }));
    expect(await screen.findByRole('dialog', { name: '快捷 AI 助教' })).toBeTruthy();
    await waitFor(() => {
      expect(chatSpy).toHaveBeenCalledTimes(1);
    });
    expect(await screen.findByText('HTTP 默认使用 80 端口。')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '关闭快捷助教' }));

    // In-place answer correction right inside PracticeTab
    fireEvent.click(screen.getByRole('button', { name: /原地修正答案/ }));
    const ansInput = screen.getByPlaceholderText('输入修正后的标准答案...');
    fireEvent.change(ansInput, { target: { value: '80 端口' } });
    fireEvent.click(screen.getByRole('button', { name: '保存修正' }));
    await waitFor(() => {
      expect(correctionSpy).toHaveBeenCalledWith(expect.objectContaining({
        id: 11,
        pending: false,
        answer: '80 端口',
      }));
    });

    // Switch to QuestionBank and back to Practice: Q2 & score must still be preserved!
    fireEvent.click(screen.getByRole('button', { name: '本地题库管理' }));
    expect(await screen.findByText('凸轮机构中从动件运动规律取决于？')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '智能刷题练习' }));
    expect(screen.getByText('HTTP 默认端口是？')).toBeTruthy();
    expect(screen.getByText('第 2 / 2 题')).toBeTruthy();
  });

  it('preserves unsaved platform edits across platform switches and supports in-place role toggles in settings', async () => {
    setupDefaultMocks();
    const saveSpy = vi.spyOn(aiApi, 'saveConfig').mockResolvedValue({
      config: mockConfig,
      server: { running: false, port: 3005, url: 'http://127.0.0.1:3005/query', ocsConfigJson: '[]' },
    });

    render(<AIWorkspace onBack={vi.fn()} initialTab="settings" />);
    expect(await screen.findByText('AI 供应商、联网搜题与协同引擎设置')).toBeTruthy();

    // Edit DeepSeek API key then switch to OpenAI: unsaved draft must NOT be wiped
    const keyInput = screen.getByPlaceholderText('输入该平台的 API Key（如 sk-...）');
    fireEvent.change(keyInput, { target: { value: 'sk-new-deepseek' } });
    expect(await screen.findByText('有未保存修改')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /OpenAI/ }));
    expect(screen.getByText('有未保存修改')).toBeTruthy();

    // Assign GPT-4o as Agent model right on the model row
    fireEvent.click(screen.getByRole('button', { name: '助教' }));

    // Switching to another tab shows global unsaved-settings reminder pill
    fireEvent.click(screen.getByRole('button', { name: '控制台与查题' }));
    expect(await screen.findByRole('button', { name: '有未保存设置' })).toBeTruthy();

    // Jump back and save
    fireEvent.click(screen.getByRole('button', { name: '有未保存设置' }));
    fireEvent.click(screen.getByRole('button', { name: /保存全部设置/ }));
    await waitFor(() => {
      expect(saveSpy).toHaveBeenCalledWith(expect.objectContaining({
        config: expect.objectContaining({
          selectedAgentModel: 'openai/gpt-4o',
        }),
      }));
    });
  });

  it('jumps directly from QuestionBank folder to Practice and supports inline pending quick-fix', async () => {
    setupDefaultMocks();
    vi.spyOn(aiApi, 'listFolders').mockResolvedValue({
      items: [
        { id: 0, name: '默认文件夹', parentId: 0, questionCount: 5 },
        { id: 2, name: '计算机网络', parentId: 0, questionCount: 3 },
      ],
    });
    vi.spyOn(aiApi, 'listQuestions').mockResolvedValue({
      items: [
        {
          id: 99,
          question: 'OSI 模型有几层？',
          options: '',
          questionType: 'short',
          answer: '6层',
          explanation: '',
          folderId: 2,
          folderName: '计算机网络',
          isAi: true,
          isPendingCorrection: true,
          mastery: 0,
        },
      ],
      total: 1,
      page: 1,
      pageSize: 30,
    });
    const fixSpy = vi.spyOn(aiApi, 'setCorrection').mockResolvedValue({ ok: true });
    const practiceSpy = vi.spyOn(aiApi, 'getPracticeQueue').mockResolvedValue({ items: [] });

    render(<AIWorkspace onBack={vi.fn()} initialTab="questions" />);
    expect(await screen.findByText('OSI 模型有几层？')).toBeTruthy();

    // Inline quick-fix pending question without opening edit modal
    fireEvent.click(screen.getByRole('button', { name: /原地速改答案/ }));
    const fixInput = screen.getByPlaceholderText(/输入修正后的标准答案/);
    fireEvent.change(fixInput, { target: { value: '7层' } });
    fireEvent.click(screen.getByRole('button', { name: '保存并解除待修正' }));
    await waitFor(() => {
      expect(fixSpy).toHaveBeenCalledWith({ id: 99, pending: false, answer: '7层' });
    });

    // Select folder "计算机网络" and click "刷当前目录"
    fireEvent.click(screen.getByRole('button', { name: '计算机网络' }));
    fireEvent.click(screen.getByRole('button', { name: /刷当前目录/ }));
    await waitFor(() => {
      expect(practiceSpy).toHaveBeenCalledWith(expect.objectContaining({ folderId: 2, mode: 'smart' }));
    });
  });

  it('omits local OCS server card and supports Chaoxing Agent tool calls in QuickTutorDrawer', async () => {
    setupDefaultMocks();
    const onAgentEffects = vi.fn();
    const onViewProgress = vi.fn();
    const chatSpy = vi.spyOn(aiApi, 'chatWithAgent').mockResolvedValue({
      session: {
        id: 'sess-agent',
        title: '帮我刷高数',
        messages: [
          { role: 'user', content: '帮我获取当前学习通账号的所有课程列表' },
          {
            role: 'assistant',
            content: '已为您启动《高等数学》自动刷课任务。',
            modelName: 'DeepSeek Chat',
            toolCalls: [
              { id: 'c1', name: 'chaoxing_list_courses', label: '获取学习通课程列表', ok: true, summary: '获取到 2 门课程', durationMs: 120 },
              { id: 'c2', name: 'chaoxing_start_study', label: '启动学习通自动刷课', ok: true, summary: '已启动刷课任务（1 门课，1.5x）', durationMs: 85 },
            ],
          },
        ],
      },
      uiEffects: [{ type: 'task_started', taskId: 'task-999', taskType: 'study' }],
    });

    render(
      <AIWorkspace
        onBack={vi.fn()}
        chaoxingContext={{ userInfo: { username: '13800138000', password: '', use_cookies: true }, activeTaskId: null, taskStatus: null }}
        onAgentEffects={onAgentEffects}
        onViewProgress={onViewProgress}
      />
    );

    expect(await screen.findByText('分层答题模型调度')).toBeTruthy();
    expect(screen.queryByText('本地 OCS 题库服务')).toBeNull();
    expect(screen.queryByText('复制 OCS 配置')).toBeNull();

    // Open QuickTutorDrawer and trigger quick Agent action
    fireEvent.click(screen.getByRole('button', { name: /快捷助教/ }));
    expect(await screen.findByRole('dialog', { name: '快捷 AI 助教' })).toBeTruthy();
    expect(screen.getByText('Agent 已连学习通')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '查看我的学习通课程' }));
    await waitFor(() => {
      expect(chatSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          enableTools: true,
          chaoxing: expect.objectContaining({ username: '13800138000', use_cookies: true }),
        }),
        expect.any(Object)
      );
    });

    expect(await screen.findByText('Agent 已执行 2 项操作')).toBeTruthy();
    expect(onAgentEffects).toHaveBeenCalledWith([{ type: 'task_started', taskId: 'task-999', taskType: 'study' }]);

    fireEvent.click(screen.getByRole('button', { name: /查看任务进度/ }));
    expect(onViewProgress).toHaveBeenCalledOnce();
  });

  it('supports live Agent progress, cancellation, inline model switching, regenerate, session rename, and markdown tables', async () => {
    setupDefaultMocks();
    vi.spyOn(aiApi, 'listAgentSessions').mockResolvedValue({
      items: [
        {
          id: 'sess-live',
          title: '原始标题',
          messages: [],
        },
      ],
    });
    const renameSpy = vi.spyOn(aiApi, 'renameAgentSession').mockResolvedValue({
      id: 'sess-live',
      title: '高等数学复习',
      messages: [],
    });

    let resolveChat;
    const chatSpy = vi.spyOn(aiApi, 'chatWithAgent').mockImplementation((params, opts) => {
      if (params.regenerate) {
        return Promise.resolve({
          session: {
            id: 'sess-live',
            title: '高等数学复习',
            messages: [
              { role: 'user', content: '查一下课程表' },
              {
                role: 'assistant',
                content: '| 课程 | 进度 |\n| --- | --- |\n| 高等数学 | 80% |',
                modelName: 'DeepSeek Chat',
                durationMs: 420,
              },
            ],
          },
          uiEffects: [],
        });
      }
      opts?.onProgress?.({
        runId: 'run-1',
        status: 'running',
        phase: 'running_tools',
        step: 2,
        maxSteps: 8,
        elapsedMs: 1200,
        activeTools: [{ id: 'a1', name: 'chaoxing_list_courses', label: '获取学习通课程列表' }],
        toolCalls: [],
      });
      return new Promise((resolve) => {
        resolveChat = resolve;
      });
    });

    render(<AIWorkspace onBack={vi.fn()} initialTab="agent" />);
    expect(await screen.findByText('原始标题')).toBeTruthy();

    // Rename session inline
    fireEvent.click(screen.getByTitle('重命名对话'));
    const titleInput = screen.getByDisplayValue('原始标题');
    fireEvent.change(titleInput, { target: { value: '高等数学复习' } });
    fireEvent.keyDown(titleInput, { key: 'Enter' });
    await waitFor(() => {
      expect(renameSpy).toHaveBeenCalledWith('sess-live', '高等数学复习');
    });

    // Pick model override
    const modelSelect = screen.getByLabelText('选择助教模型');
    fireEvent.change(modelSelect, { target: { value: 'deepseek/deepseek-chat' } });

    // Send message and verify live progress card appears
    const textarea = screen.getByPlaceholderText(/输入题目/);
    fireEvent.change(textarea, { target: { value: '查一下课程表' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(await screen.findByText(/正在执行工具：获取学习通课程列表/)).toBeTruthy();
    expect(screen.getByText('第 2/8 步')).toBeTruthy();

    // Resolve first response with Markdown table
    resolveChat({
      session: {
        id: 'sess-live',
        title: '高等数学复习',
        messages: [
          { role: 'user', content: '查一下课程表' },
          {
            role: 'assistant',
            content: '| 课程 | 进度 |\n| --- | --- |\n| 高等数学 | 75% |',
            modelName: 'DeepSeek Chat',
            durationMs: 500,
          },
        ],
      },
      uiEffects: [],
    });

    expect(await screen.findByText('75%')).toBeTruthy();
    expect(screen.getByRole('table')).toBeTruthy();
    expect(chatSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        sessionId: 'sess-live',
        message: '查一下课程表',
        modelId: 'deepseek/deepseek-chat',
      }),
      expect.objectContaining({
        onProgress: expect.any(Function),
        signal: expect.any(AbortSignal),
      })
    );

    // Click regenerate and verify updated reply
    fireEvent.click(screen.getByRole('button', { name: /重新生成/ }));
    await waitFor(() => {
      expect(chatSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          sessionId: 'sess-live',
          regenerate: true,
        }),
        expect.any(Object)
      );
    });
    expect(await screen.findByText('80%')).toBeTruthy();
  });
});
