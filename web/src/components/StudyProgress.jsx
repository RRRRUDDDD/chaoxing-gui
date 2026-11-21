import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Button from './ui/Button';
import CountUp from './CountUp';
import RepositoryLink from './RepositoryLink';
import ExecutionLog from './ExecutionLog';
import CourseToolProgress, { formatToolAmount } from './CourseToolProgress';
import { courseToolLabels } from './CourseToolSettings';
import { ScheduledShutdownBanner, ShutdownStatusText } from './ShutdownBanner';
import { ActiveVideoJobs, CourseDetailsList } from './ProgressSections';
import {
  Loader2, ArrowLeft, CheckCircle2, AlertCircle,
  Play, Clock, ChevronDown, MonitorPlay,
  StopCircle, Power,
} from 'lucide-react';
import api from '../api/axios';
import { isTerminalStatus, startTaskPolling } from '../lib/taskPolling';

const previewDetails = {
  courses: [
    {
      id: 'preview-001',
      title: '大学英语（演示课程）',
      status: 'completed',
      chapters: [
        { id: 'chapter-1', title: '第一章：课程导学', has_finished: true },
        { id: 'chapter-2', title: '第二章：基础内容', has_finished: true },
      ],
    },
    {
      id: 'preview-002',
      title: '高等数学（演示课程）',
      status: 'completed',
      chapters: [
        { id: 'chapter-3', title: '第一章：函数与极限', has_finished: true },
        { id: 'chapter-4', title: '第二章：导数与微分', has_finished: true },
      ],
    },
  ],
};
const previewStatus = () => {
  const courses = previewDetails.courses;
  const chapters = courses.flatMap((course) => course.chapters);
  const completed = chapters.filter((chapter) => chapter.has_finished).length;
  return {
    status: 'completed', progress: courses.filter((course) => course.status === 'completed').length, total: courses.length,
    stats: { completed_chapters: completed, total_chapters: chapters.length,
      completed_tasks: completed, total_tasks: chapters.length, failed_tasks: 0, skipped_tasks: 0 },
    start_time: Date.now() / 1000 - 372,
  };
};
const previewLogs = [
  { seq: 1, timestamp: Date.now() / 1000 - 60, level: 'success', message: '演示任务已完成' },
  { seq: 2, timestamp: Date.now() / 1000 - 30, level: 'info', message: '所有课程均已处理完毕' },
];

const StudyProgressContent = ({ taskId, username, notice = '', onBack, onStatus, onMissing, onStop, onStartStudy, starting = false, startError = '', actionsDisabled = false, recovering = false, recoveryError = '', onRetryRecovery, preview = false }) => {
  const [taskStatus, setTaskStatus] = useState(() => (preview ? previewStatus() : null));
  const [taskDetails, setTaskDetails] = useState(() => (preview ? previewDetails : null));
  const [logs, setLogs] = useState(() => (preview ? previewLogs : []));
  const [logsTruncated, setLogsTruncated] = useState(false);
  const [finalDetailsReady, setFinalDetailsReady] = useState(preview);
  const [pollError, setPollError] = useState('');
  const [missing, setMissing] = useState(false);
  const [stopConfirming, setStopConfirming] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [stopError, setStopError] = useState('');
  const [cancellingShutdown, setCancellingShutdown] = useState(false);
  const [cancelShutdownError, setCancelShutdownError] = useState('');
  const stopConfirmTimer = useRef(null);
  const cancelShutdownController = useRef(null);
  const actionGeneration = useRef(0);
  const [expandedCourses, setExpandedCourses] = useState(() => new Set());
  const callbacks = useRef({ onStatus, onMissing });
  useEffect(() => {
    callbacks.current = { onStatus, onMissing };
  });
  const stopPending = stopping || taskStatus?.cancel_requested === true;
  const isToolTask = ['visits', 'catalog', 'video_time', 'read_time', 'download'].includes(taskStatus?.task_type);

  useEffect(() => {
    const gen = actionGeneration;
    const stopTimer = stopConfirmTimer;
    const cancelCtrl = cancelShutdownController;
    return () => {
      gen.current += 1;
      clearTimeout(stopTimer.current);
      stopTimer.current = null;
      cancelCtrl.current?.abort();
      cancelCtrl.current = null;
    };
  }, []);

  useEffect(() => {
    if (preview || recovering || recoveryError) return undefined;
    let observedStatus;
    return startTaskPolling({
      api, taskId,
      onStatus: (status) => {
        observedStatus = status.status;
        setTaskStatus(status);
        setFinalDetailsReady((current) => (isTerminalStatus(status.status) ? current : false));
        callbacks.current.onStatus?.(status);
      },
      onDetails: (details) => {
        setTaskDetails(details);
        setFinalDetailsReady(isTerminalStatus(observedStatus));
      },
      onLogs: (state) => { setLogs(state.logs); setLogsTruncated(state.truncated); },
      onError: setPollError,
      onMissing: () => { setMissing(true); setPollError(''); callbacks.current.onMissing?.(); },
    });
  }, [taskId, preview, recovering, recoveryError]);

  const clearStopConfirm = () => {
    clearTimeout(stopConfirmTimer.current);
    stopConfirmTimer.current = null;
    setStopConfirming(false);
  };

  // A stop cannot be undone, so the first click only arms the button.
  const handleStopClick = async () => {
    if (stopPending || !onStop) return;
    if (!stopConfirming) {
      setStopError('');
      setStopConfirming(true);
      clearTimeout(stopConfirmTimer.current);
      stopConfirmTimer.current = setTimeout(() => {
        stopConfirmTimer.current = null;
        setStopConfirming(false);
      }, 5000);
      return;
    }
    clearStopConfirm();
    setStopping(true);
    setStopError('');
    const generation = actionGeneration.current;
    try {
      await onStop();
    } catch (error) {
      if (generation !== actionGeneration.current) return;
      setStopError(error?.response?.data?.msg || error?.message || '停止任务失败，请重试');
      setStopping(false);
    }
  };

  const toggleCourse = useCallback((courseId) => {
    setExpandedCourses((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(courseId)) newSet.delete(courseId);
      else newSet.add(courseId);
      return newSet;
    });
  }, []);

  const getStatusInfo = () => {
    if (recovering) return { text: '恢复中', cls: 'text-brand', icon: Loader2 };
    if (recoveryError) return { text: '等待恢复', cls: 'text-warning-ink', icon: AlertCircle };
    if (missing) return { text: '已过期', cls: 'text-faint', icon: Clock };
    if (!taskStatus) return { text: '加载中', cls: 'text-faint', icon: Clock };

    switch (taskStatus.status) {
      case 'running':
        return stopPending
          ? { text: '正在停止', cls: 'text-faint', icon: Loader2 }
          : { text: isToolTask ? '执行中' : '学习中', cls: 'text-brand', icon: Play };
      case 'interrupted':
        return { text: '等待恢复', cls: 'text-warning-ink', icon: Clock };
      case 'completed':
        return { text: '已完成', cls: 'text-success-ink', icon: CheckCircle2 };
      case 'error':
        return { text: '出现错误', cls: 'text-danger', icon: AlertCircle };
      case 'partial':
        return { text: '部分完成', cls: 'text-warning-ink', icon: AlertCircle };
      case 'cancelled':
        return { text: '已停止', cls: 'text-faint', icon: StopCircle };
      default:
        return { text: '未知状态', cls: 'text-faint', icon: Clock };
    }
  };

  const statusInfo = getStatusInfo();
  const knownTotal = Number.isFinite(taskStatus?.total) && taskStatus.total > 0;
  const progress = knownTotal ? Math.max(0, Math.min(100, ((taskStatus.progress || 0) / taskStatus.total) * 100)) : 0;
  const terminal = isTerminalStatus(taskStatus?.status) || missing;
  const rawActiveJobs = taskDetails?.active_jobs;
  // Object.entries keeps the backend's stable courseId:jobid keys so a reordered
  // poll cannot make the progress bar animation morph into the wrong row.
  const activeJobs = useMemo(
    () => (!terminal && rawActiveJobs ? Object.entries(rawActiveJobs) : []),
    [terminal, rawActiveJobs],
  );
  // Offer the stop only where it can act: a live task, not a preview, and not
  // while recovery still owns the task.
  const canStop = !!onStop && !preview && !terminal && !recovering && !recoveryError
    && ['running', 'interrupted'].includes(taskStatus?.status);

  const finished = taskStatus?.status === 'completed';
  const errored = taskStatus?.status === 'error';
  const partial = taskStatus?.status === 'partial';
  const cancelled = taskStatus?.status === 'cancelled';
  const shutdownInfo = taskStatus?.shutdown;
  const shutdownScheduled = !missing && shutdownInfo?.state === 'scheduled';
  const shutdownCancelled = !missing && shutdownInfo?.state === 'cancelled';
  const shutdownFailed = !missing && shutdownInfo?.state === 'failed';
  const shutdownArmed = !missing && !terminal && taskStatus?.shutdown_on_complete === true && !shutdownCancelled;

  const handleCancelShutdown = useCallback(async () => {
    if (preview || actionsDisabled || cancellingShutdown || !username || !taskId) return;
    cancelShutdownController.current?.abort();
    const controller = new AbortController();
    cancelShutdownController.current = controller;
    const generation = actionGeneration.current;
    setCancellingShutdown(true);
    setCancelShutdownError('');
    try {
      const response = await api.post(
        `/task/${encodeURIComponent(taskId)}/cancel-shutdown`,
        { username },
        { signal: controller.signal },
      );
      if (generation !== actionGeneration.current || controller.signal.aborted) return;
      if (!response.data.status) throw new Error(response.data.msg || '取消关机失败，请重试');
      const nextShutdown = { state: response.data.data?.state || 'cancelled' };
      setTaskStatus((prev) => {
        if (!prev) return prev;
        const next = { ...prev, shutdown_on_complete: false, shutdown: nextShutdown };
        callbacks.current.onStatus?.(next);
        return next;
      });
    } catch (error) {
      if (generation !== actionGeneration.current || controller.signal.aborted) return;
      if (error?.response?.status === 404) {
        setMissing(true);
        callbacks.current.onMissing?.();
        return;
      }
      setCancelShutdownError(error?.response?.data?.msg || error?.message || '取消关机失败，可在命令提示符运行 shutdown /a 手动取消');
    } finally {
      if (generation === actionGeneration.current && cancelShutdownController.current === controller) {
        cancelShutdownController.current = null;
        setCancellingShutdown(false);
      }
    }
  }, [preview, actionsDisabled, cancellingShutdown, username, taskId]);

  // 读取列表只是中间步骤：资源可选时把视觉重心留给“继续处理”，返回入口降为次级。
  const catalogReadyForSelection = taskStatus?.task_type === 'catalog' && finalDetailsReady && ['completed', 'partial'].includes(taskStatus.status);
  const unverifiedCourses = !!taskDetails?.courses?.some((course) => course.verification && course.verification.status !== 'confirmed');

  const goToResources = () => {
    const target = document.getElementById('resource-selection');
    if (!target) return;
    target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    target.focus({ preventScroll: true });
  };

  return (
    <div className="min-h-screen bg-canvas">
      {/* 顶栏 */}
      <header className="sticky top-0 z-20 border-b border-line bg-white/85 backdrop-blur">
        <div className="page-shell flex h-16 items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-brand">
              <MonitorPlay className="h-5 w-5 text-white" aria-hidden="true" />
            </div>
            <div className="min-w-0">
              <h1 className="truncate text-[15px] font-semibold leading-tight">{isToolTask ? `${taskStatus.task_label || courseToolLabels[taskStatus.task_type]}进度` : '学习进度监控'}</h1>
              <p className="truncate text-xs leading-tight text-faint">实时跟踪任务执行详情</p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <RepositoryLink />
            {canStop && (
              <Button
                variant={stopConfirming ? 'destructive' : 'outline'}
                size="sm"
                onClick={handleStopClick}
                onBlur={clearStopConfirm}
                disabled={stopPending}
                aria-label={stopConfirming ? '确认停止任务' : '停止任务'}
              >
                {stopPending
                  ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                  : <StopCircle className="h-3.5 w-3.5" aria-hidden="true" />}
                {stopPending ? '正在停止' : stopConfirming ? '确认停止' : '停止任务'}
              </Button>
            )}
            <Button onClick={onBack} size="sm" variant={terminal && !catalogReadyForSelection ? 'default' : 'outline'}>
              <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
              返回课程选择
            </Button>
          </div>
        </div>
      </header>

      <main className="page-shell py-[clamp(1.5rem,2.5vw,3rem)]">
        {recovering && (
          <p role="status" className="mb-5 rounded-xl bg-brand-soft px-5 py-4 text-sm text-body">正在恢复上次学习任务，并核对已完成的进度…</p>
        )}
        {recoveryError && (
          <div role="alert" className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-xl bg-warning/10 px-5 py-4 text-sm text-body">
            <span>{recoveryError}</span>
            <Button variant="outline" size="sm" onClick={onRetryRecovery}>重试恢复任务</Button>
          </div>
        )}
        {taskStatus?.recovery_error && (
          <div role="alert" className="mb-5 rounded-xl bg-warning/10 px-5 py-4 text-sm text-body">{taskStatus.recovery_error}</div>
        )}
        {notice && (
          <div role="alert" className="mb-5 rounded-xl bg-warning/10 px-5 py-4 text-sm text-body">
            {notice}
          </div>
        )}
        {startError && taskStatus?.task_type !== 'catalog' && (
          <div role="alert" className="mb-5 rounded-xl bg-danger/5 px-5 py-4 text-sm text-danger">{startError}</div>
        )}
        {stopError && (
          <div role="alert" className="mb-5 rounded-xl bg-warning/10 px-5 py-4 text-sm text-body">{stopError}</div>
        )}
        {stopPending && taskStatus?.status === 'running' && (
          <p role="status" className="mb-5 rounded-xl bg-brand-soft px-5 py-4 text-sm text-body">已请求停止，正在结束进行中的任务点…</p>
        )}
        {shutdownScheduled && (
          <ScheduledShutdownBanner
            shutdownInfo={shutdownInfo}
            cancelling={cancellingShutdown}
            cancelError={cancelShutdownError}
            disabled={actionsDisabled}
            onCancel={handleCancelShutdown}
          />
        )}
        {shutdownArmed && (
          <div role="status" className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-warning/25 bg-warning/5 px-5 py-3.5 animate-fade-in">
            <div className="flex min-w-0 items-start gap-2.5">
              <Power className="mt-0.5 h-4 w-4 shrink-0 text-warning-ink" aria-hidden="true" />
              <div className="min-w-0">
                <p className="text-sm font-medium text-body">已开启完成后自动关机，刷取任务结束后将倒计时 60 秒自动关机。</p>
                {cancelShutdownError && <p className="mt-1 text-xs text-danger">{cancelShutdownError}</p>}
              </div>
            </div>
            <Button variant="outline" size="sm" onClick={handleCancelShutdown} disabled={actionsDisabled || cancellingShutdown}>
              {cancellingShutdown && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
              {cancellingShutdown ? '正在取消' : '取消关机'}
            </Button>
          </div>
        )}
        {shutdownCancelled && (
          <p role="status" className="mb-5 rounded-xl border border-line bg-soft px-5 py-3.5 text-sm text-body animate-fade-in">
            已取消自动关机，系统不会自动关闭。
          </p>
        )}
        {shutdownFailed && (
          <div role="alert" className="mb-5 flex items-start gap-2.5 rounded-xl bg-danger/5 px-5 py-4 text-sm text-danger animate-fade-in">
            <Power className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            <span>自动关机启动失败：{shutdownInfo.error || '系统拒绝了关机请求'}</span>
          </div>
        )}
        {(missing || pollError) && (
          <div role="alert" className="mb-5 rounded-xl bg-warning/10 px-5 py-4 text-sm text-body">
            {missing ? '任务不存在或已过期，请返回课程选择。' : `${pollError}，正在重试获取最新数据。`}
          </div>
        )}
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_clamp(20rem,24vw,28rem)] lg:items-start 2xl:gap-8">
          {/* 侧栏：当前进度 → 任务信息 → 详细统计；移动端优先展示进度摘要；
              lg 起固定在视口内跟随滚动，内容超出视口时在栏内滚动 */}
          <aside className="order-first space-y-6 animate-stagger-up lg:order-2 lg:sticky lg:top-20 lg:max-h-[calc(100vh-6rem)] lg:overflow-y-auto lg:overscroll-contain" style={{ animationDelay: '200ms' }}>
            {/* 当前进度 */}
            <section className="rounded-xl border border-line bg-white p-5 shadow-card">
              <div className="mb-4 flex flex-wrap items-baseline justify-between gap-3">
                <h2 className="text-[15px] font-semibold">当前进度</h2>
                <span className={`text-sm font-semibold ${statusInfo.cls}`}>{statusInfo.text}</span>
              </div>
              <p className="mb-3 text-sm text-body tnum">
                {knownTotal ? `${taskStatus.progress || 0} / ${taskStatus.total} ${isToolTask ? '条目' : '课程'} · ${Math.round(progress)}%` : '等待进度总量'}
              </p>
              {!isToolTask && taskStatus?.stats && (
                <p className="mb-3 text-xs text-body">
                  章节 {taskStatus.stats.completed_chapters || 0} / {taskStatus.stats.total_chapters || 0}
                  {!!(taskStatus.stats.failed_chapters || taskStatus.stats.failed_tasks || taskStatus.stats.failed_courses || taskStatus.stats.skipped_chapters || taskStatus.stats.skipped_tasks) && (
                    <span className="ml-3 text-warning-ink">存在失败或跳过项，请核对下方详细统计</span>
                  )}
                </p>
              )}
              <div
                className="h-2.5 w-full overflow-hidden rounded-full bg-soft"
                role="progressbar"
                aria-valuenow={knownTotal ? Math.round(progress) : undefined}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label={isToolTask ? '整体任务进度' : '整体学习进度'}
              >
                <div
                  className="h-full rounded-full bg-brand transition-[width] duration-700 ease-out"
                  style={{ width: `${Math.min(progress, 100)}%` }}
                />
              </div>

              {!terminal && taskStatus?.current_course && (
                <div className="mt-5 flex items-start gap-3.5 rounded-lg border border-line bg-soft/60 p-4">
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-white shadow-card">
                    <Loader2 className="h-4.5 w-4.5 animate-spin text-brand" aria-hidden="true" />
                  </div>
                  <div className="min-w-0">
                    <p className="text-xs font-medium text-brand">{isToolTask ? '正在处理' : '正在学习'}</p>
                    <h3 className="mt-0.5 truncate text-[15px] font-semibold" title={taskStatus.current_course}>
                      {taskStatus.current_course}
                    </h3>
                    {taskStatus.current_chapter && (
                      <p className="mt-0.5 flex items-center gap-1.5 text-[13px] text-body">
                        <span className="rounded bg-brand-soft px-1.5 py-0.5 text-[11px] font-medium text-brand">章节</span>
                        <span className="min-w-0 break-words" title={taskStatus.current_chapter}>{taskStatus.current_chapter}</span>
                      </p>
                    )}
                    {isToolTask && taskStatus.current_task && <p className="mt-1 break-words text-xs text-faint">{taskStatus.current_task}</p>}
                  </div>
                </div>
              )}

              {/* 错误 */}
              {taskStatus?.error && (
                <div role="alert" className="mt-5 flex items-start gap-2.5 rounded-lg bg-danger/5 px-4 py-3 animate-fade-in">
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-danger" aria-hidden="true" />
                  <div>
                    <p className="text-[13px] font-semibold text-danger">错误信息</p>
                    <p className="mt-0.5 text-[13px] leading-relaxed text-danger/90">{taskStatus.error}</p>
                  </div>
                </div>
              )}
            </section>

            {/* 任务信息 */}
            <section className="rounded-xl border border-line bg-white shadow-card">
              <div className="border-b border-line px-5 py-3.5">
                <h2 className="text-[15px] font-semibold">任务信息</h2>
              </div>
              <dl className="space-y-4 p-5">
                {isToolTask && <div>
                  <dt className="text-xs text-faint">{taskStatus.task_type === 'catalog' ? '已读取资源' : '累计执行量'}</dt>
                  <dd className="mt-1 text-sm font-medium">{taskStatus.task_type === 'catalog' ? taskDetails?.tool?.resources?.length || 0 : formatToolAmount(taskDetails?.tool?.completed_units ?? 0, taskDetails?.tool?.unit)}</dd>
                </div>}
                <div>
                  <dt className="text-xs text-faint">任务 ID</dt>
                  <dd className="mt-1 break-all rounded-lg bg-soft px-2.5 py-2 font-mono text-xs">
                    {taskId}
                  </dd>
                </div>
                <div>
                  <dt className="text-xs text-faint">开始时间</dt>
                  <dd className="mt-1 text-sm font-medium tnum">
                    {taskStatus?.start_time
                      ? new Date(taskStatus.start_time * 1000).toLocaleString('zh-CN')
                      : '-'}
                  </dd>
                </div>
                {(shutdownArmed || shutdownScheduled || shutdownCancelled || shutdownFailed) && (
                  <div>
                    <dt className="text-xs text-faint">完成后关机</dt>
                    <dd className={`mt-1 text-sm font-medium tnum ${shutdownFailed ? 'text-danger' : shutdownCancelled ? 'text-faint' : 'text-warning-ink'}`}>
                      <ShutdownStatusText
                        shutdownScheduled={shutdownScheduled}
                        shutdownArmed={shutdownArmed}
                        shutdownCancelled={shutdownCancelled}
                        shutdownInfo={shutdownInfo}
                      />
                    </dd>
                  </div>
                )}
              </dl>
            </section>

            {/* 详细统计 */}
            {!isToolTask && taskStatus?.stats && (
              <section className="rounded-xl border border-line bg-white shadow-card">
                <div className="border-b border-line px-5 py-3.5">
                  <h2 className="text-[15px] font-semibold">详细统计</h2>
                </div>
                <dl className="divide-y divide-line px-5">
                  {/* 汇总项常驻，异常项仅在非零时显示 */}
                  {[
                    ['总章节数', taskStatus.stats.total_chapters, '', true],
                    ['已完成章节（含无任务）', taskStatus.stats.completed_chapters, 'text-success-ink', true],
                    ['无任务章节', taskStatus.stats.empty_chapters, ''],
                    ['失败章节', taskStatus.stats.failed_chapters, 'text-danger'],
                    ['跳过章节', taskStatus.stats.skipped_chapters, 'text-warning-ink'],
                    ['失败课程', taskStatus.stats.failed_courses, 'text-danger'],
                    ['总任务数', taskStatus.stats.total_tasks, '', true],
                    ['已完成任务', taskStatus.stats.completed_tasks, 'text-success-ink', true],
                    ['失败任务', taskStatus.stats.failed_tasks, 'text-danger'],
                    ['跳过任务', taskStatus.stats.skipped_tasks, 'text-warning-ink'],
                  ].filter(([, value, , always]) => always || value > 0).map(([label, value, color]) => (
                    <div key={label} className="flex items-center justify-between py-3">
                      <dt className="text-[13px] text-faint">{label}</dt>
                      <dd className={`text-sm font-semibold tnum ${color}`}>
                        <CountUp value={value} />
                      </dd>
                    </div>
                  ))}
                </dl>
              </section>
            )}
          </aside>

          {/* 主栏 */}
          <div className="min-w-0 space-y-6 lg:order-1">
            {finished && !missing && (
              <div className="flex items-start gap-2.5 rounded-xl border border-success/25 bg-success/5 p-4 animate-fade-in" role="status">
                <CheckCircle2 className="mt-0.5 h-4.5 w-4.5 shrink-0 text-success-ink" aria-hidden="true" />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold text-success-ink">{taskStatus.task_type === 'catalog' ? '资源列表已读取' : '所有任务已完成'}</p>
                  <p className="mt-0.5 text-[13px] text-body">{isToolTask ? taskStatus.task_type === 'catalog' ? '请勾选资源后执行下一步' : '请查看执行结果和日志' : '所选课程已按当前配置处理完成，请核对平台进度'}</p>
                </div>
                {catalogReadyForSelection && (
                  <Button size="sm" className="shrink-0 self-center" onClick={goToResources}>
                    前往选择资源
                    <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
                  </Button>
                )}
              </div>
            )}

            {partial && !missing && (
              <div className="flex items-start gap-2.5 rounded-xl border border-warning/25 bg-warning/5 p-4 animate-fade-in" role="status">
                <AlertCircle className="mt-0.5 h-4.5 w-4.5 shrink-0 text-warning-ink" aria-hidden="true" />
                <div>
                  <p className="text-sm font-semibold text-warning-ink">任务已结束，部分内容未完成</p>
                  <p className="mt-0.5 text-[13px] text-body">{isToolTask ? '请查看执行结果和日志，核对已完成内容后再开始新任务' : unverifiedCourses ? '部分章节未完成或平台完成状态未确认，请查看课程明细和日志' : '部分章节失败或被跳过，请查看课程明细和日志'}</p>
                </div>
              </div>
            )}

            {errored && !missing && (
              <div className="flex items-start gap-2.5 rounded-xl border border-danger/25 bg-danger/5 p-4 animate-fade-in" role="status">
                <AlertCircle className="mt-0.5 h-4.5 w-4.5 shrink-0 text-danger" aria-hidden="true" />
                <div>
                  <p className="text-sm font-semibold text-danger">任务执行失败</p>
                  <p className="mt-0.5 text-[13px] text-body">请查看错误信息或日志排查原因</p>
                </div>
              </div>
            )}

            {cancelled && !missing && (
              <div className="flex items-start gap-2.5 rounded-xl border border-line bg-soft p-4 animate-fade-in" role="status">
                <StopCircle className="mt-0.5 h-4.5 w-4.5 shrink-0 text-faint" aria-hidden="true" />
                <div>
                  <p className="text-sm font-semibold text-body">任务已手动停止</p>
                  <p className="mt-0.5 text-[13px] text-body">已完成的进度已保留，可返回课程选择开始新任务</p>
                </div>
              </div>
            )}

            {isToolTask && (
              <CourseToolProgress
                key={`${username || ''}:${taskId}`}
                taskId={taskId} username={username} taskStatus={taskStatus} tool={taskDetails?.tool}
                catalogReady={finalDetailsReady} onStartStudy={onStartStudy} starting={starting} startError={startError}
                disabled={actionsDisabled || recovering || !!recoveryError || missing}
              />
            )}

            <ActiveVideoJobs activeJobs={activeJobs} />

            <CourseDetailsList
              courses={taskDetails?.courses}
              expandedCourses={expandedCourses}
              onToggleCourse={toggleCourse}
            />

            <ExecutionLog key={`logs:${username || ''}:${taskId}`} logs={logs} truncated={logsTruncated} />
          </div>
        </div>
      </main>
    </div>
  );
};

// 任务、账号或恢复阶段切换时通过 key 重挂载，彻底取消旧轮询与旧操作并避免 effect 内同步重置状态。
const StudyProgress = (props) => {
  const identity = `${props.username || ''}:${props.taskId || ''}:${props.preview === true}:${props.recovering === true}:${Boolean(props.recoveryError)}`;
  return <StudyProgressContent key={identity} {...props} />;
};

export default StudyProgress;
