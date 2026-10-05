export const logLevel = (level) => String(level || 'info').toLowerCase() === 'warn'
  ? 'warning' : String(level || 'info').toLowerCase();

export function filterLogs(logs, level = 'all', query = '') {
  const keyword = query.trim().toLocaleLowerCase();
  return logs.filter((log) => {
    const severity = logLevel(log.level);
    const matchesLevel = level === 'all' || (level === 'errors' ? severity === 'error' : ['error', 'warning'].includes(severity));
    return matchesLevel && String(log.message ?? '').toLocaleLowerCase().includes(keyword);
  });
}

// Best-effort export protection, not a guarantee that arbitrary logs are safe to share.
export function redactLogText(text) {
  return String(text)
    .replace(/\b(https?:\/\/)[^\s/]+@/gi, '$1[REDACTED]@')
    .replace(/\b((?:set-cookie|cookie|authorization|proxy-authorization)["']?\s*[=:]\s*)[^\r\n]+/gi, '$1[REDACTED]')
    .replace(/(["']?(?:password|passwd|pwd|token|access[_-]?token|refresh[_-]?token|api[_-]?key|secret|client[_-]?secret)["']?\s*(?:=|:)\s*)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s&,;}]+)/gi, '$1[REDACTED]')
    .replace(/\bBearer\s+[A-Za-z0-9._~+/-]+=*/gi, 'Bearer [REDACTED]');
}

export function logTime(timestamp) {
  const date = new Date(Number(timestamp) * 1000);
  return Number.isFinite(date.getTime()) ? date.toLocaleTimeString('zh-CN') : '--:--:--';
}

export function formatLogsForCopy(logs) {
  return logs.map((log) => `[${logTime(log.timestamp)}] [${logLevel(log.level)}] ${redactLogText(log.message ?? '')}`).join('\n');
}
