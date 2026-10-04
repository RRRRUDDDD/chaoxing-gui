import React, { useEffect, useMemo, useRef, useState } from 'react';
import Button from './ui/Button';
import { LOG_LIMIT } from '../lib/taskPolling';
import { filterLogs, formatLogsForCopy, logLevel, logTime } from '../lib/logPresentation';

const levelColors = { error: 'text-red-300', warning: 'text-amber-300', success: 'text-green-300' };
const copyNotice = '已遮盖常见敏感字段，分享前仍需检查';

const ExecutionLog = ({ logs, truncated }) => {
  const [level, setLevel] = useState('all');
  const [query, setQuery] = useState('');
  const [following, setFollowing] = useState(true);
  const [copyStatus, setCopyStatus] = useState('');
  const [manualText, setManualText] = useState(null);
  const [copying, setCopying] = useState(false);
  const containerRef = useRef(null);
  const manuallyPaused = useRef(false);
  const mounted = useRef(true);
  const filtered = useMemo(() => filterLogs(logs, level, query), [logs, level, query]);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    const container = containerRef.current;
    if (container && following) container.scrollTop = container.scrollHeight;
  }, [filtered, following]);

  const copy = async () => {
    if (copying) return;
    const text = formatLogsForCopy(filtered);
    setCopying(true);
    setManualText(null);
    try {
      if (!navigator.clipboard?.writeText) throw new Error('unavailable');
      await navigator.clipboard.writeText(text);
      if (mounted.current) setCopyStatus(`已复制当前结果。${copyNotice}`);
    } catch (error) {
      if (mounted.current) {
        setManualText(text);
        setCopyStatus(`${error?.message === 'unavailable' ? '当前环境不支持剪贴板' : '剪贴板写入被拒绝或失败'}，请手动复制下方文本。${copyNotice}`);
      }
    } finally {
      if (mounted.current) setCopying(false);
    }
  };

  return (
    <section aria-label="日志工具" className="min-w-0 rounded-xl border border-line bg-white shadow-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-5 py-3.5">
        <h2 className="text-[15px] font-semibold">执行日志</h2>
        <span className="text-xs text-faint">最近 {LOG_LIMIT} 条</span>
      </div>
      <div className="space-y-3 p-4">
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-body">级别
            <select aria-label="日志级别" value={level} onChange={(event) => setLevel(event.target.value)}
              className="h-9 rounded-lg border border-line bg-white px-2 focus-visible:ring-4 focus-visible:ring-brand/20">
              <option value="all">全部</option>
              <option value="warnings">错误与警告</option>
              <option value="errors">仅错误</option>
            </select>
          </label>
          <input type="search" aria-label="搜索日志" placeholder="搜索日志关键词" value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="h-9 min-w-0 flex-1 basis-40 rounded-lg border border-line px-3 text-xs placeholder:text-faint focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-brand/20" />
          <Button variant="outline" size="sm" onClick={copy} disabled={!filtered.length || copying}>
            {copying ? '复制中' : '复制当前结果'}
          </Button>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-faint">
          <span role="status">匹配 {filtered.length} / {logs.length} 条</span>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={following} className="h-4 w-4 accent-brand"
              onChange={(event) => { manuallyPaused.current = !event.target.checked; setFollowing(event.target.checked); }} />
            自动跟随
          </label>
          <Button variant="ghost" size="sm" onClick={() => {
            manuallyPaused.current = false;
            setFollowing(true);
            if (containerRef.current) containerRef.current.scrollTop = containerRef.current.scrollHeight;
          }}>回到最新</Button>
          {!following && <span>跟随已暂停</span>}
        </div>
        {truncated && <p className="text-xs text-faint">较早的日志已省略，仅保留最近 {LOG_LIMIT} 条。</p>}
        <div ref={containerRef} role="log" aria-label="执行日志" aria-live="off" tabIndex={0}
          onScroll={(event) => {
            const container = event.currentTarget;
            if (!manuallyPaused.current) setFollowing(container.scrollHeight - container.scrollTop - container.clientHeight <= 24);
          }}
          className="min-h-[8rem] max-h-[clamp(16rem,48vh,42rem)] overflow-y-auto overscroll-contain rounded-lg bg-gray-900 p-4 font-mono text-xs leading-relaxed [overflow-wrap:anywhere] focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-brand/30">
          {!filtered.length ? <p className="text-gray-300">{logs.length ? '没有匹配的日志，请调整级别或关键词。' : '等待日志输出...'}</p> : filtered.map((log) => (
            <div key={log.seq} className="mb-0.5 whitespace-pre-wrap">
              <span className="mr-2 text-gray-400 tnum">[{logTime(log.timestamp)}]</span>
              <span className="mr-2 text-gray-400">[{logLevel(log.level)}]</span>
              <span className={levelColors[logLevel(log.level)] || 'text-gray-300'}>{log.message}</span>
            </div>
          ))}
        </div>
        {copyStatus && <p role="status" className="text-xs text-body">{copyStatus}</p>}
        {manualText !== null && (
          <div className="space-y-2">
            <textarea aria-label="手动复制日志" readOnly value={manualText} rows={6}
              onFocus={(event) => event.target.select()}
              className="w-full rounded-lg border border-line p-3 font-mono text-xs focus-visible:ring-4 focus-visible:ring-brand/20" />
            <Button size="sm" variant="ghost" onClick={() => setManualText(null)}>关闭手动复制</Button>
          </div>
        )}
      </div>
    </section>
  );

};
export default ExecutionLog;
