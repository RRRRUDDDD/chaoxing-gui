import React, { useEffect, useMemo, useRef, useState } from 'react';
import { BookOpen, Download, FileText, FolderOpen, Loader2, MonitorPlay, Search } from 'lucide-react';
import api from '../api/axios';
import { validTaskId } from '../lib/sessionStore';
import { isTerminalStatus, resultLabels } from '../lib/taskPolling';
import Button from './ui/Button';
import Input from './ui/Input';
import Label from './ui/Label';
import { courseToolLabels, validateMinutes } from './CourseToolSettings';

const kinds = { video: '视频', audio: '音频', document: '文档', file: '文件' };
const sectionCls = 'rounded-xl border border-line bg-white p-5 shadow-card';

export function formatToolAmount(value, unit = '') {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '未知';
  const number = Math.max(0, Number(value));
  if (unit === '字节') {
    const scale = number > 0 ? Math.min(3, Math.floor(Math.log(number) / Math.log(1024))) : 0;
    return `${(number / (1024 ** scale)).toLocaleString('zh-CN', { maximumFractionDigits: 1 })} ${['B', 'KB', 'MB', 'GB'][Math.max(0, scale)]}`;
  }
  return `${number.toLocaleString('zh-CN', { maximumFractionDigits: unit === '分钟' ? 2 : 1 })}${unit ? ` ${unit}` : ''}`;
}

function UnitProgress({ label, completed, total, unit }) {
  const knownTotal = Number.isFinite(Number(total)) && Number(total) > 0;
  const percent = knownTotal ? Math.min(100, Math.max(0, (Number(completed) || 0) / Number(total) * 100)) : 0;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2 text-sm">
        <span className="min-w-0 break-words text-body">{label}</span>
        <span className="text-xs text-faint tnum">{formatToolAmount(completed, unit)} / {formatToolAmount(total, unit)}</span>
      </div>
      <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={knownTotal ? Math.round(percent) : undefined} className="h-2 overflow-hidden rounded-full bg-soft">
        <div className="h-full rounded-full bg-brand transition-[width] duration-700" style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}

function CourseToolContent({ taskId, username, taskStatus, tool, catalogReady = false, onStartStudy, starting = false, startError = '', disabled = false }) {
  const [selectedIds, setSelectedIds] = useState([]);
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState('all');
  const [minutes, setMinutes] = useState('30');
  const [submitting, setSubmitting] = useState(false);
  const [selectionError, setSelectionError] = useState('');
  const [opening, setOpening] = useState(false);
  const [openError, setOpenError] = useState('');
  const [openNotice, setOpenNotice] = useState('');
  const startController = useRef(null);
  const openController = useRef(null);

  useEffect(() => () => {
    startController.current?.abort();
    openController.current?.abort();
  }, []);

  const taskType = taskStatus.task_type;
  const purpose = tool?.purpose;
  const readingCatalog = purpose === 'reading_time';
  const durationTask = purpose === 'video_time' || readingCatalog;
  const readingTask = taskType === 'reading_time';
  const durationId = readingCatalog ? 'reading-minutes' : 'video-minutes';
  const StartIcon = readingCatalog ? BookOpen : purpose === 'video_time' ? MonitorPlay : Download;
  const resources = useMemo(() => {
    const items = Array.isArray(tool?.resources) ? tool.resources : [];
    return purpose === 'video_time' ? items.filter((item) => item.kind === 'video') : items;
  }, [tool?.resources, purpose]);
  const results = Array.isArray(tool?.results) ? tool.results : [];
  const courseIds = new Set((Array.isArray(tool?.course_ids) ? tool.course_ids : []).map(String));
  const available = (resource) => typeof resource.id === 'string' && !!resource.id
    && courseIds.has(String(resource.course_id))
    && (readingCatalog ? resource.readable === true : purpose === 'video_time' ? resource.watchable === true : purpose === 'download' && resource.downloadable === true);
  const selected = resources.filter((resource) => selectedIds.includes(resource.id) && available(resource));
  const filtered = resources.filter((resource) => {
    const matchesKind = kind === 'all' || resource.kind === kind;
    const searchText = [resource.name, resource.course_title, resource.chapter_title].filter(Boolean).join(' ').toLowerCase();
    return matchesKind && searchText.includes(query.trim().toLowerCase());
  });
  const minutesError = durationTask ? validateMinutes(minutes) : '';
  const ready = catalogReady && ['completed', 'partial'].includes(taskStatus.status);
  const busy = starting || submitting;
  const canSelect = ready && !busy && !disabled;
  const canStart = canSelect && !!onStartStudy && validTaskId(taskId) && selected.length > 0 && !minutesError;
  const hasDownloads = results.some((result) => result.status === 'completed' && result.path);
  const canOpen = taskType === 'download' && !!tool?.output_dir && hasDownloads && !!username && validTaskId(taskId) && !disabled;

  const handleStart = async () => {
    if (!canStart || startController.current) return;
    const controller = new AbortController();
    startController.current = controller;
    setSubmitting(true);
    setSelectionError('');
    try {
      await onStartStudy({
        task_type: purpose,
        course_list: [...new Set(selected.map((resource) => String(resource.course_id)))],
        tool_options: {
          source_task_id: taskId,
          resource_ids: selected.map((resource) => resource.id),
          ...(durationTask ? { minutes: Number(minutes) } : {}),
        },
      });
    } catch (error) {
      if (!controller.signal.aborted) setSelectionError(error?.response?.data?.msg || error?.message || '启动任务失败，请重试');
    } finally {
      if (!controller.signal.aborted && startController.current === controller) {
        startController.current = null;
        setSubmitting(false);
      }
    }
  };

  const handleOpen = async () => {
    if (!canOpen || openController.current) return;
    const controller = new AbortController();
    openController.current = controller;
    setOpening(true);
    setOpenError('');
    setOpenNotice('');
    try {
      const response = await api.post(`/task/${encodeURIComponent(taskId)}/open-downloads`, { username }, { signal: controller.signal });
      if (controller.signal.aborted || openController.current !== controller) return;
      if (!response.data.status) throw new Error(response.data.msg || '无法打开下载目录');
      setOpenNotice('已打开下载目录');
    } catch (error) {
      if (!controller.signal.aborted && openController.current === controller) {
        setOpenError(`打开下载目录失败：${error?.response?.data?.msg || error?.message || '请重试'}`);
      }
    } finally {
      if (!controller.signal.aborted && openController.current === controller) {
        openController.current = null;
        setOpening(false);
      }
    }
  };

  return (
    <>
      <section className={sectionCls} aria-label="工具执行进度">
        <h2 className="mb-4 text-[15px] font-semibold">{taskStatus.task_label || courseToolLabels[taskType]}执行进度</h2>
        <UnitProgress label={readingTask ? '已上报时长' : '累计进度'} completed={tool?.completed_units ?? 0} total={tool?.total_units} unit={tool?.unit} />
        {readingTask && <p className="mt-3 text-xs leading-relaxed text-faint">已上报时长表示成功发送的阅读记录覆盖的时长。平台当天统计可能次日更新，请以平台统计为准。</p>}
        {tool?.current && (
          <div className="mt-4 rounded-lg bg-soft/60 p-4">
            <UnitProgress label={tool.current.name || '当前条目'} completed={tool.current.completed ?? 0} total={tool.current.total} unit={tool.current.unit || tool.unit} />
          </div>
        )}
        {taskType === 'download' && (
          <div className="mt-5 space-y-3 border-t border-line pt-4">
            {tool?.output_dir && <p className="break-all text-xs text-faint">下载目录：{tool.output_dir}</p>}
            <Button variant="outline" onClick={handleOpen} disabled={!canOpen || opening}>
              {opening ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <FolderOpen className="h-4 w-4" aria-hidden="true" />}
              {opening ? '正在打开' : '打开下载目录'}
            </Button>
            {!hasDownloads && <p className="text-xs text-faint">文件保存完成后可打开下载目录。</p>}
            {openError && <p role="alert" className="rounded-lg bg-danger/5 px-3 py-2 text-sm text-danger">{openError}</p>}
            {openNotice && <p role="status" className="text-xs text-success">{openNotice}</p>}
          </div>
        )}
      </section>

      {taskType === 'catalog' && (
        <section className={sectionCls} aria-label="资源选择">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <h2 className="flex items-center gap-2 text-[15px] font-semibold">
              <FileText className="h-4 w-4 text-brand" aria-hidden="true" />
              {readingCatalog ? '阅读任务列表' : purpose === 'video_time' ? '视频列表' : '资源列表'}
            </h2>
            <span className="text-xs text-faint tnum">已选 {selected.length} / 共 {resources.length}</span>
          </div>
          <p className="mb-4 text-xs leading-relaxed text-faint">
            {ready ? taskStatus.status === 'partial' ? '部分资源读取失败，可选择已读取的资源继续；详情见执行结果和日志。' : '请勾选需要处理的资源。'
              : ['completed', 'partial'].includes(taskStatus.status) ? '正在获取最终资源列表…'
                : isTerminalStatus(taskStatus.status) ? '本次读取未完成，请返回课程选择重新读取。' : '正在读取资源，请等待任务结束后选择。'}
          </p>
          {readingCatalog && <p className="mb-4 text-xs leading-relaxed text-faint">每个勾选任务将读取其关联书籍，按实际时间在应用后台运行，可随时停止。平台当天统计可能次日更新。</p>}
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <div className="relative min-w-40 flex-1">
              <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-faint" aria-hidden="true" />
              <Input type="search" aria-label="搜索资源" placeholder="搜索名称、课程或章节" value={query} onChange={(event) => setQuery(event.target.value)} className="pl-9" />
            </div>
            {purpose === 'download' && (
              <select aria-label="资源类型" value={kind} onChange={(event) => setKind(event.target.value)} className="h-10 rounded-lg border border-line bg-white px-3 text-sm focus:border-brand focus:outline-none focus:ring-4 focus:ring-brand/15">
                <option value="all">全部类型</option>
                {Object.entries(kinds).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            )}
          </div>
          <div className="mb-2 flex gap-2">
            <Button variant="ghost" size="sm" disabled={!canSelect || !filtered.some(available)} onClick={() => setSelectedIds((previous) => [...new Set([...previous, ...filtered.filter(available).map((resource) => resource.id)])])}>全选当前列表</Button>
            <Button variant="ghost" size="sm" disabled={!canSelect || !selected.length} onClick={() => setSelectedIds([])}>清空选择</Button>
          </div>
          <ul className="max-h-[32rem] divide-y divide-line overflow-y-auto rounded-lg border border-line scroll-brutal">
            {filtered.map((resource) => (
              <li key={resource.id}>
                <label className={`flex items-start gap-3 p-3.5 ${canSelect && available(resource) ? 'cursor-pointer hover:bg-soft' : 'text-faint'}`}>
                  <input
                    type="checkbox" aria-label={`选择资源 ${resource.name}`}
                    checked={selected.some((item) => item.id === resource.id)}
                    disabled={!canSelect || !available(resource)}
                    onChange={(event) => setSelectedIds((previous) => event.target.checked ? [...new Set([...previous, resource.id])] : previous.filter((id) => id !== resource.id))}
                    className="mt-0.5 h-4 w-4 shrink-0 rounded accent-brand focus-visible:ring-4 focus-visible:ring-brand/15"
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block break-words text-sm font-medium">{resource.name}</span>
                    <span className="mt-1 block text-xs text-faint">{resource.course_title} · {resource.chapter_title}</span>
                    <span className="mt-1 block text-xs text-faint">
                      {readingCatalog ? '阅读任务' : kinds[resource.kind] || '资源'}
                      {resource.duration != null && ` · ${formatToolAmount(resource.duration, '秒')}`}
                      {!available(resource) && (readingCatalog ? ' · 不可阅读' : purpose === 'video_time' ? ' · 不可累计时长' : ' · 不可下载')}
                    </span>
                    {readingCatalog && (
                      <span className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-body">
                        <span>平台现有：{formatToolAmount(resource.read_minutes, '分钟')}</span>
                        <span>要求：{formatToolAmount(resource.required_minutes, '分钟')}</span>
                        <span>书籍：{formatToolAmount(resource.book_count, '本')}</span>
                      </span>
                    )}
                  </span>
                </label>
              </li>
            ))}
            {!filtered.length && <li className="p-8 text-center text-sm text-faint">{resources.length ? '没有匹配的资源' : ready ? readingCatalog ? '未找到阅读任务，请查看执行结果或选择其他课程。' : '未找到可用资源，请查看执行结果或选择其他课程。' : '暂无资源'}</li>}
          </ul>

          {durationTask && (
            <div className="mt-5 space-y-1.5">
              <Label htmlFor={durationId}>{readingCatalog ? '每个阅读任务增加时长（分钟）' : '每个视频增加时长（分钟）'}</Label>
              <Input id={durationId} type="number" min="0.1" max="1440" step="any" value={minutes} disabled={!canSelect} onChange={(event) => setMinutes(event.target.value)} aria-invalid={!!minutesError} aria-describedby={`${durationId}-hint`} />
              <p id={`${durationId}-hint`} role={minutesError ? 'alert' : undefined} className={`text-xs ${minutesError ? 'text-danger' : 'text-faint'}`}>
                {minutesError || (readingCatalog ? '范围 0.1–1440 分钟。按实际时间运行，可随时停止。' : '范围 0.1–1440 分钟。按实际时间运行，超出视频长度后从头继续。')}
              </p>
            </div>
          )}
          <div className="mt-5 flex flex-wrap items-center gap-3">
            <Button onClick={handleStart} disabled={!canStart}>
              {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <StartIcon className="h-4 w-4" aria-hidden="true" />}
              {busy ? '任务启动中' : readingCatalog ? '开始阅读' : purpose === 'video_time' ? '开始累计时长' : '下载所选资源'}
            </Button>
            {!selected.length && <p className="text-xs text-faint">请至少勾选一项可用资源</p>}
          </div>
          {(startError || selectionError) && <p role="alert" className="mt-3 rounded-lg bg-danger/5 px-3 py-2 text-sm text-danger">{startError || selectionError}</p>}
        </section>
      )}

      {results.length > 0 && (
        <section className={sectionCls} aria-label="工具执行结果">
          <h2 className="mb-4 text-[15px] font-semibold">执行结果</h2>
          <ul className="divide-y divide-line">
            {results.map((result, index) => (
              <li key={`${result.id || index}-${index}`} className="space-y-2 py-3 first:pt-0 last:pb-0">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="break-words text-sm font-medium">{result.name}</p>
                    <p className="mt-0.5 text-xs text-faint">{result.course_title}</p>
                  </div>
                  <span className={`shrink-0 text-xs ${result.status === 'error' ? 'text-danger' : result.status === 'completed' ? 'text-success' : 'text-warning'}`}>{readingTask && result.status === 'completed' ? '上报完成' : resultLabels[result.status] || result.status}</span>
                </div>
                {result.message && <p className={`whitespace-pre-wrap text-xs leading-relaxed ${result.status === 'error' ? 'text-danger' : 'text-body'}`}>{result.message}</p>}
                {taskType === 'visits' && (
                  <dl className="grid gap-2 rounded-lg bg-soft p-3 text-xs sm:grid-cols-3">
                    <div><dt className="text-faint">已提交次数</dt><dd className="mt-1 font-medium tnum">{formatToolAmount(result.submitted, '次')}</dd></div>
                    <div><dt className="text-faint">平台次数（开始）</dt><dd className="mt-1 font-medium tnum">{result.before == null ? '不可用' : formatToolAmount(result.before, '次')}</dd></div>
                    <div><dt className="text-faint">平台次数（结束）</dt><dd className="mt-1 font-medium tnum">{result.after == null ? '不可用' : formatToolAmount(result.after, '次')}</dd></div>
                  </dl>
                )}
                {readingTask && (
                  <dl className="grid gap-2 rounded-lg bg-soft p-3 text-xs sm:grid-cols-2">
                    <div><dt className="text-faint">平台阅读时长（开始）</dt><dd className="mt-1 font-medium tnum">{result.before == null ? '不可用' : formatToolAmount(result.before, '分钟')}</dd></div>
                    <div><dt className="text-faint">平台阅读时长（结束）</dt><dd className="mt-1 font-medium tnum">{result.after == null ? '不可用' : formatToolAmount(result.after, '分钟')}</dd></div>
                  </dl>
                )}
                {result.seconds != null && <p className="text-xs text-body tnum">{readingTask ? '已上报时长' : '已记录时长'}：{formatToolAmount(result.seconds, '秒')}</p>}
                {result.bytes != null && <p className="text-xs text-body tnum">已保存：{formatToolAmount(result.bytes, '字节')}</p>}
                {result.path && <p className="break-all text-xs text-faint">{result.path}</p>}
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

// Account/task changes must discard selections and cancel in-flight folder actions.
export default function CourseToolProgress(props) {
  return <CourseToolContent key={`${props.username || ''}:${props.taskId}`} {...props} />;
}
