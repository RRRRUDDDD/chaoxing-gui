export const LOG_LIMIT = 500;
export const isTerminalStatus = (status) => ['completed', 'error', 'partial'].includes(status);

export function appendLogPage(previous, page, limit = LOG_LIMIT) {
  const incoming = new Map();
  for (const log of Array.isArray(page.data) ? page.data : []) {
    if (Number.isSafeInteger(log?.seq) && log.seq > previous.cursor) incoming.set(log.seq, log);
  }
  const fresh = [...incoming.values()].sort((a, b) => a.seq - b.seq);
  const logs = fresh.length ? [...previous.logs, ...fresh] : previous.logs;
  return {
    logs: logs.length > limit ? logs.slice(-limit) : logs,
    cursor: Math.max(previous.cursor, Number.isSafeInteger(page.next_cursor) ? page.next_cursor : 0, fresh.at(-1)?.seq || 0),
    truncated: previous.truncated || page.truncated === true || logs.length > limit,
  };
}

function responseBody(response) {
  if (!response.data?.status) throw new Error(response.data?.msg || '获取任务信息失败');
  return response.data;
}

// A round includes status and all dependent snapshots. Schedule only after it settles;
// a terminal status still needs its final details/logs, including retries on failure.
export function startTaskPolling({
  api, taskId, includeDetails = true, intervalMs = 2000,
  onStatus = () => {}, onDetails = () => {}, onLogs = () => {},
  onError = () => {}, onMissing = () => {},
}) {
  const controller = new AbortController();
  const { signal } = controller;
  const taskPath = `/task/${encodeURIComponent(taskId)}`;
  let timer;
  let logState = { logs: [], cursor: 0, truncated: false };
  const stop = () => {
    controller.abort();
    clearTimeout(timer);
  };
  const missing = () => { stop(); onMissing(); };

  const poll = async () => {
    let finished = false;
    try {
      const status = responseBody(await api.get(taskPath, { signal })).data;
      if (signal.aborted) return;
      onStatus(status);
      finished = isTerminalStatus(status.status);

      if (includeDetails) {
        const results = await Promise.allSettled([
          api.get(`${taskPath}/details`, { signal }).then(responseBody),
          api.get(`/logs/${encodeURIComponent(taskId)}`, { signal, params: { after: logState.cursor } }).then(responseBody),
        ]);
        if (signal.aborted) return;
        if (results.some((result) => result.status === 'rejected' && result.reason?.response?.status === 404)) {
          missing();
          return;
        }
        if (results[0].status === 'fulfilled') onDetails(results[0].value.data);
        if (results[1].status === 'fulfilled') {
          logState = appendLogPage(logState, results[1].value);
          onLogs(logState);
        }
        const failure = results.find((result) => result.status === 'rejected');
        if (failure) throw failure.reason;
      }
      onError('');
    } catch (error) {
      if (signal.aborted) return;
      if (error?.response?.status === 404) {
        missing();
        return;
      }
      finished = false;
      onError(error?.response?.data?.msg || error?.message || '获取任务信息失败，将自动重试');
    }
    if (!signal.aborted && !finished) timer = setTimeout(poll, intervalMs);
  };

  poll();
  return stop;
}

export function chapterState(chapter) {
  if (chapter.status === 'failed') return 'error';
  return chapter.status || (chapter.has_finished ? 'completed' : 'pending');
}

export const resultLabels = {
  pending: '等待中', running: '进行中', completed: '已完成',
  error: '失败', partial: '部分完成', skipped: '已跳过', empty: '无任务',
};
