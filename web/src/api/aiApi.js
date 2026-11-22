import api from './axios';

export async function aiRpc(action, params = {}, { timeout = 30000, signal } = {}) {
  const response = await api.post(
    '/ai/rpc',
    { action, params },
    { timeout, signal }
  );
  const body = response?.data;
  if (!body || body.status !== true) {
    throw new Error(body?.msg || `AI 请求失败 (${action})`);
  }
  return body.data;
}

function makeCancelledError() {
  const err = new Error('已停止本次生成');
  err.name = 'AbortError';
  err.cancelled = true;
  return err;
}

function sleepWithSignal(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(makeCancelledError());
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(makeCancelledError());
    };
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

async function runAgentWithProgress(params, { onProgress, signal, timeout = 180000 } = {}) {
  let started;
  try {
    started = await aiRpc('agent.chat.start', params, { timeout: 25000, signal });
  } catch (err) {
    if (signal?.aborted || err?.name === 'CanceledError' || err?.name === 'AbortError') {
      throw makeCancelledError();
    }
    if (/未知 AI 操作/.test(String(err?.message || ''))) {
      return aiRpc('agent.chat', params, { timeout, signal });
    }
    throw err;
  }

  const runId = started?.runId;
  if (!runId) {
    return aiRpc('agent.chat', params, { timeout, signal });
  }

  const sendCancel = () => {
    aiRpc('agent.chat.cancel', { runId }, { timeout: 5000 }).catch(() => {});
  };
  const onAbort = () => sendCancel();
  signal?.addEventListener('abort', onAbort, { once: true });

  const deadline = Date.now() + Math.max(30000, timeout);
  try {
    while (true) {
      if (signal?.aborted) {
        sendCancel();
        throw makeCancelledError();
      }
      if (Date.now() > deadline) {
        sendCancel();
        throw new Error('Agent 执行超时，已自动停止，请稍后重试');
      }
      const snap = await aiRpc('agent.chat.poll', { runId }, { timeout: 15000, signal });
      onProgress?.(snap);
      if (snap?.status === 'completed') {
        return snap.result;
      }
      if (snap?.status === 'cancelled') {
        throw makeCancelledError();
      }
      if (snap?.status === 'error') {
        throw new Error(snap.error || 'AI 助教响应失败');
      }
      await sleepWithSignal(320, signal);
    }
  } catch (err) {
    if (signal?.aborted || err?.name === 'CanceledError' || err?.name === 'AbortError' || err?.cancelled) {
      sendCancel();
      throw makeCancelledError();
    }
    throw err;
  } finally {
    signal?.removeEventListener('abort', onAbort);
  }
}

export const aiApi = {
  // Config & models
  getConfig: (opts) => aiRpc('config.get', {}, opts),
  saveConfig: (payload, opts) => aiRpc('config.save', payload, opts),
  getSummary: (opts) => aiRpc('config.summary', {}, opts),
  getPresetPlatforms: (opts) => aiRpc('presets.platforms', {}, opts),
  testModel: (params, opts) => aiRpc('models.test', params, { timeout: 60000, ...opts }),
  testSearch: (params, opts) => aiRpc('search.test', params, { timeout: 30000, ...opts }),

  // Local OCS HTTP server
  getServerStatus: (opts) => aiRpc('server.status', {}, opts),
  toggleServer: (params, opts) => aiRpc('server.toggle', params, opts),

  // Query engine & logs & stats
  runQuery: (params, opts) => aiRpc('query.run', params, { timeout: 140000, ...opts }),
  listLogs: (limit = 80, opts) => aiRpc('logs.list', { limit }, opts),
  clearLogs: (opts) => aiRpc('logs.clear', {}, opts),
  getStats: (opts) => aiRpc('stats.get', {}, opts),

  // Folders
  listFolders: (opts) => aiRpc('folders.list', {}, opts),
  createFolder: (name, parentId = 0, opts) => aiRpc('folders.create', { name, parentId }, opts),
  renameFolder: (id, name, opts) => aiRpc('folders.rename', { id, name }, opts),
  moveFolder: (id, targetParentId, opts) => aiRpc('folders.move', { id, targetParentId }, opts),
  deleteFolder: (id, deleteQuestions = false, opts) => aiRpc('folders.delete', { id, deleteQuestions }, opts),
  clearFolder: (id, opts) => aiRpc('folders.clear', { id }, opts),

  // Questions
  listQuestions: (params = {}, opts) => aiRpc('questions.list', params, opts),
  saveQuestion: (question, opts) => aiRpc('questions.save', question, opts),
  deleteQuestions: (ids, opts) => aiRpc('questions.delete', { ids }, opts),
  moveQuestions: (ids, targetFolderId, opts) => aiRpc('questions.move', { ids, targetFolderId }, opts),
  setCorrection: (params, opts) => aiRpc('questions.correction', params, opts),
  importQuestions: (params, opts) => aiRpc('questions.import', params, { timeout: 60000, ...opts }),
  exportQuestions: (params, opts) => aiRpc('questions.export', params, { timeout: 60000, ...opts }),

  // Practice
  getPracticeQueue: (params = {}, opts) => aiRpc('practice.next', params, opts),
  submitPractice: (params, opts) => aiRpc('practice.submit', params, opts),
  explainQuestion: (questionId, userAnswer = '', opts) =>
    aiRpc('practice.explain', { questionId, userAnswer }, { timeout: 120000, ...opts }),
  getPracticeHistory: (limit = 50, opts) => aiRpc('practice.history', { limit }, opts),

  // Study graph
  listSubjects: (opts) => aiRpc('study.subjects.list', {}, opts),
  saveSubject: (params, opts) => aiRpc('study.subjects.save', params, opts),
  deleteSubject: (id, opts) => aiRpc('study.subjects.delete', { id }, opts),
  getStudyGraph: (subjectId, opts) => aiRpc('study.graph.get', { subjectId }, opts),
  saveGraphNode: (params, opts) => aiRpc('study.graph.node.save', params, opts),
  deleteGraphNode: (id, opts) => aiRpc('study.graph.node.delete', { id }, opts),
  saveGraphEdge: (params, opts) => aiRpc('study.graph.edge.save', params, opts),
  deleteGraphEdge: (id, opts) => aiRpc('study.graph.edge.delete', { id }, opts),
  linkQuestionNode: (params, opts) => aiRpc('study.graph.link', params, opts),
  reviewGraphNode: (nodeId, quality, opts) => aiRpc('study.graph.review', { nodeId, quality }, opts),
  generateStudyGraph: (params, opts) => aiRpc('study.graph.generate', params, { timeout: 140000, ...opts }),

  // AI Tutor Agent
  listAgentSessions: (opts) => aiRpc('agent.sessions.list', {}, opts),
  createAgentSession: (title = '新对话', opts) => aiRpc('agent.sessions.create', { title }, opts),
  renameAgentSession: (id, title, opts) => aiRpc('agent.sessions.rename', { id, title }, opts),
  clearAgentSession: (id, opts) => aiRpc('agent.sessions.clear', { id }, opts),
  deleteAgentSession: (id, opts) => aiRpc('agent.sessions.delete', { id }, opts),
  startAgentRun: (params, opts) => aiRpc('agent.chat.start', params, opts),
  pollAgentRun: (runId, opts) => aiRpc('agent.chat.poll', { runId }, opts),
  cancelAgentRun: (runId, opts) => aiRpc('agent.chat.cancel', { runId }, opts),
  chatWithAgent: (params, opts = {}) => (
    typeof opts?.onProgress === 'function'
      ? runAgentWithProgress(params, opts)
      : aiRpc('agent.chat', params, { timeout: 140000, ...opts })
  ),
};
