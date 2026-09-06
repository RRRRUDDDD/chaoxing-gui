import React, { useState, useEffect, useRef } from 'react';
import Button from './ui/Button';
import CountUp from './CountUp';
import {
  Loader2, ArrowLeft, CheckCircle2, XCircle, AlertCircle,
  Play, Clock, ChevronDown, ChevronRight, BookOpen, MonitorPlay,
  FileText, Home, Github,
} from 'lucide-react';
import api from '../api/axios';
import { LOG_LIMIT, chapterState, isTerminalStatus, resultLabels, startTaskPolling } from '../lib/taskPolling';

const StudyProgress = ({ taskId, notice = '', onBack, onStatus, onMissing, preview = false }) => {
  const [taskStatus, setTaskStatus] = useState(preview ? {
    status: 'completed',
    progress: 3,
    total: 3,
    stats: {
      completed_chapters: 12,
      total_chapters: 12,
      completed_tasks: 12,
      total_tasks: 12,
      failed_tasks: 0,
      skipped_tasks: 0,
    },
    start_time: Date.now() / 1000 - 372,
  } : null);
  const [taskDetails, setTaskDetails] = useState(preview ? {
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
  } : null);
  const [logs, setLogs] = useState(preview ? [
    { seq: 1, timestamp: Date.now() / 1000 - 60, level: 'success', message: '演示任务已完成' },
    { seq: 2, timestamp: Date.now() / 1000 - 30, level: 'info', message: '所有课程均已处理完毕' },
  ] : []);
  const [logsTruncated, setLogsTruncated] = useState(false);
  const [pollError, setPollError] = useState('');
  const [missing, setMissing] = useState(false);
  const [expandedCourses, setExpandedCourses] = useState(new Set());
  const logsEndRef = useRef(null);
  const callbacks = useRef({ onStatus, onMissing });
  callbacks.current = { onStatus, onMissing };

  useEffect(() => {
    if (preview) return undefined;
    setTaskStatus(null);
    setTaskDetails(null);
    setLogs([]);
    setLogsTruncated(false);
    setPollError('');
    setMissing(false);
    setExpandedCourses(new Set());
    return startTaskPolling({
      api, taskId,
      onStatus: (status) => { setTaskStatus(status); callbacks.current.onStatus?.(status); },
      onDetails: setTaskDetails,
      onLogs: (state) => { setLogs(state.logs); setLogsTruncated(state.truncated); },
      onError: setPollError,
      onMissing: () => { setMissing(true); setPollError(''); callbacks.current.onMissing?.(); },
    });
  }, [taskId, preview]);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'end' });
  }, [logs]);

  const toggleCourse = (courseId) => {
    setExpandedCourses((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(courseId)) {
        newSet.delete(courseId);
      } else {
        newSet.add(courseId);
      }
      return newSet;
    });
  };

  const getStatusInfo = () => {
    if (missing) return { text: '已过期', cls: 'text-faint', icon: Clock };
    if (!taskStatus) return { text: '加载中', cls: 'text-faint', icon: Clock };

    switch (taskStatus.status) {
      case 'running':
        return { text: '学习中', cls: 'text-brand', icon: Play };
      case 'completed':
        return { text: '已完成', cls: 'text-success', icon: CheckCircle2 };
      case 'error':
        return { text: '出现错误', cls: 'text-danger', icon: AlertCircle };
      case 'partial':
        return { text: '部分完成', cls: 'text-warning', icon: AlertCircle };
      default:
        return { text: '未知状态', cls: 'text-faint', icon: Clock };
    }
  };

  const getLogLevelColor = (level) => {
    switch (level) {
      case 'error':
        return 'text-[#f87171]';
      case 'warning':
        return 'text-[#fbbf24]';
      case 'success':
        return 'text-[#4ade80]';
      default:
        return 'text-gray-300';
    }
  };

  const getCourseStatusIcon = (status) => {
    switch (status) {
      case 'running':
        return <Loader2 className="h-4 w-4 text-brand animate-spin" aria-hidden="true" />;
      case 'completed':
        return <CheckCircle2 className="h-4 w-4 text-success" aria-hidden="true" />;
      case 'empty':
        return <FileText className="h-4 w-4 text-faint" aria-hidden="true" />;
      case 'partial':
      case 'skipped':
        return <AlertCircle className="h-4 w-4 text-warning" aria-hidden="true" />;
      case 'error':
        return <XCircle className="h-4 w-4 text-danger" aria-hidden="true" />;
      default:
        return <Clock className="h-4 w-4 text-faint" aria-hidden="true" />;
    }
  };

  const statusInfo = getStatusInfo();
  const StatusIcon = statusInfo.icon;
  const progress = taskStatus ? (taskStatus.progress / (taskStatus.total || 1)) * 100 : 0;
  const terminal = isTerminalStatus(taskStatus?.status) || missing;
  const activeJobs = !terminal && taskDetails?.active_jobs ? Object.values(taskDetails.active_jobs) : [];

  const formatTime = (seconds) => {
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  };

  const finished = taskStatus?.status === 'completed';
  const errored = taskStatus?.status === 'error';
  const partial = taskStatus?.status === 'partial';
  const resultColor = (state) => state === 'error' ? 'text-danger' : ['skipped', 'partial'].includes(state) ? 'text-warning' : state === 'completed' ? 'text-success' : 'text-faint';

  const statCards = [
    {
      label: '已处理课程',
      icon: BookOpen,
      iconCls: 'bg-brand-soft text-brand',
      value: (
        <>
          <CountUp value={taskStatus?.progress || 0} />
          <span className="ml-1 text-base font-medium text-faint">/ {taskStatus?.total || 0}</span>
        </>
      ),
    },
    {
      label: '章节统计',
      icon: FileText,
      iconCls: 'bg-success/10 text-success',
      value: (
        <>
          <CountUp value={taskStatus?.stats?.completed_chapters || 0} />
          <span className="ml-1 text-base font-medium text-faint">/ {taskStatus?.stats?.total_chapters || 0}</span>
        </>
      ),
    },
    {
      label: '处理进度',
      icon: Clock,
      iconCls: 'bg-warning/10 text-warning',
      value: <CountUp value={Math.round(progress)} suffix="%" />,
    },
    {
      label: '任务状态',
      icon: statusInfo.icon,
      iconCls: 'bg-soft text-body',
      value: (
        <span className={`flex items-center gap-1.5 text-lg font-semibold ${statusInfo.cls}`}>
          <StatusIcon className={`h-4.5 w-4.5 ${taskStatus?.status === 'running' ? 'animate-pulse' : ''}`} aria-hidden="true" />
          {statusInfo.text}
        </span>
      ),
    },
  ];

  return (
    <div className="min-h-screen bg-canvas">
      {/* 顶栏 */}
      <header className="sticky top-0 z-20 border-b border-line bg-white/85 backdrop-blur">
        <div className="page-shell flex h-16 items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand">
              <MonitorPlay className="h-5 w-5 text-white" aria-hidden="true" />
            </div>
            <div>
              <p className="text-[15px] font-semibold leading-tight">学习进度监控</p>
              <p className="text-xs leading-tight text-faint">实时跟踪任务执行详情</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <a
              href="https://github.com/RRRRUDDDD/chaoxing-gui"
              target="_blank"
              rel="noreferrer"
              aria-label="在 GitHub 上查看项目"
              title="在 GitHub 上查看项目"
              className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-faint transition-colors duration-150 hover:bg-soft hover:text-ink focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-brand/20"
            >
              <Github className="h-4 w-4" aria-hidden="true" />
            </a>
            {terminal && (
              <Button onClick={onBack} size="sm">
                <Home className="h-3.5 w-3.5" aria-hidden="true" />
                返回首页
              </Button>
            )}
          </div>
        </div>
      </header>

      <main className="page-shell py-[clamp(1.5rem,2.5vw,3rem)]">
        {notice && (
          <div role="alert" className="mb-5 rounded-xl bg-warning/10 px-5 py-4 text-sm text-body">
            {notice}
          </div>
        )}
        {(missing || pollError) && (
          <div role="alert" className="mb-5 rounded-xl bg-warning/10 px-5 py-4 text-sm text-body">
            {missing ? '任务不存在或已过期，请返回课程选择。' : `${pollError}，正在重试获取最新数据。`}
          </div>
        )}
        {/* 统计卡片 */}
        <section
          aria-label="任务统计"
          className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-4 animate-stagger-up"
        >
          {statCards.map((card, i) => {
            const Icon = card.icon;
            return (
              <div
                key={card.label}
                className="rounded-xl border border-line bg-white p-5 shadow-card"
                style={{ animationDelay: `${i * 60}ms` }}
              >
                <div className="flex items-start justify-between">
                  <div>
                    <p className="text-[13px] text-faint">{card.label}</p>
                    <p className="mt-1.5 text-2xl font-semibold tracking-tight leading-none tnum">
                      {card.value}
                    </p>
                  </div>
                  <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${card.iconCls}`}>
                    <Icon className="h-4.5 w-4.5" aria-hidden="true" />
                  </div>
                </div>
              </div>
            );
          })}
        </section>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_clamp(20rem,24vw,28rem)] lg:items-start 2xl:gap-8">
          {/* 主栏 */}
          <div className="min-w-0 space-y-6">
            {/* 当前进度 */}
            <section className="rounded-xl border border-line bg-white p-5 shadow-card animate-stagger-up" style={{ animationDelay: '120ms' }}>
              <div className="mb-4 flex items-baseline justify-between">
                <h2 className="text-[15px] font-semibold">当前进度</h2>
                <span className="text-xs text-faint tnum">
                  {taskStatus?.progress || 0} / {taskStatus?.total || 0} 课程
                </span>
              </div>
              <div
                className="h-2.5 w-full overflow-hidden rounded-full bg-soft"
                role="progressbar"
                aria-valuenow={Math.round(progress)}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label="整体学习进度"
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
                    <p className="text-xs font-medium text-brand">正在学习</p>
                    <h3 className="mt-0.5 truncate text-[15px] font-semibold" title={taskStatus.current_course}>
                      {taskStatus.current_course}
                    </h3>
                    {taskStatus.current_chapter && (
                      <p className="mt-0.5 flex items-center gap-1.5 text-[13px] text-body">
                        <span className="rounded bg-brand-soft px-1.5 py-0.5 text-[11px] font-medium text-brand">章节</span>
                        <span className="truncate">{taskStatus.current_chapter}</span>
                      </p>
                    )}
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

            {/* 视频播放进度 */}
            {activeJobs.length > 0 && (
              <section className="rounded-xl border border-line bg-white p-5 shadow-card animate-stagger-up" style={{ animationDelay: '180ms' }}>
                <h2 className="mb-4 flex items-center gap-2 text-[15px] font-semibold">
                  <MonitorPlay className="h-4 w-4 text-brand" aria-hidden="true" />
                  正在播放视频
                  <span className="rounded-full bg-brand-soft px-2 py-0.5 text-xs font-medium text-brand tnum">
                    {activeJobs.length}
                  </span>
                </h2>
                <div className="space-y-3">
                  {activeJobs.map((job, index) => (
                    <div
                      key={index}
                      className="rounded-lg border border-line p-4 transition-shadow duration-200 hover:shadow-card"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <h3 className="truncate text-sm font-medium" title={job.job_name}>
                            {job.job_name || '未知任务'}
                          </h3>
                          <p className="mt-0.5 truncate text-xs text-faint" title={job.course_name}>
                            {job.course_name}
                          </p>
                        </div>
                        <span className="shrink-0 text-[15px] font-semibold text-brand tnum">
                          {Math.round(job.progress)}%
                        </span>
                      </div>
                      <div className="mt-2.5">
                        <div className="mb-1 flex items-center justify-between font-mono text-[11px] text-faint tnum">
                          <span>{formatTime(job.current_time)}</span>
                          <span>{formatTime(job.duration)}</span>
                        </div>
                        <div className="h-1.5 overflow-hidden rounded-full bg-soft">
                          <div
                            className="h-full rounded-full bg-brand transition-[width] duration-700 ease-out"
                            style={{ width: `${Math.min(job.progress, 100)}%` }}
                          />
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* 课程明细 */}
            {taskDetails?.courses?.length > 0 && (
              <section className="rounded-xl border border-line bg-white shadow-card animate-stagger-up" style={{ animationDelay: '240ms' }}>
                <div className="flex items-center gap-2 border-b border-line px-5 py-3.5">
                  <BookOpen className="h-4 w-4 text-brand" aria-hidden="true" />
                  <h2 className="text-[15px] font-semibold">课程明细</h2>
                </div>
                <div className="divide-y divide-line px-2.5 py-2">
                  {taskDetails.courses.map((course) => {
                    const open = expandedCourses.has(course.id);
                    const done = course.chapters ? course.chapters.filter((c) => ['completed', 'empty'].includes(chapterState(c))).length : 0;
                    const total = course.chapters?.length || 0;
                    return (
                      <div key={course.id}>
                        <button
                          type="button"
                          onClick={() => toggleCourse(course.id)}
                          aria-expanded={open}
                          className="flex w-full items-center gap-3 rounded-lg px-2.5 py-3 text-left transition-colors duration-150 hover:bg-soft focus-visible:bg-soft focus-visible:outline-none"
                        >
                          {getCourseStatusIcon(course.status)}
                          <span className="min-w-0 flex-1 truncate text-sm font-medium">{course.title}</span>
                          <span className={`shrink-0 text-xs ${resultColor(course.status)}`}>{resultLabels[course.status] || '等待中'}</span>
                          {total > 0 && (
                            <span className="shrink-0 rounded-full bg-soft px-2 py-0.5 text-xs text-faint tnum">
                              {done} / {total} 章节
                            </span>
                          )}
                          {open ? (
                            <ChevronDown className="h-4 w-4 shrink-0 text-faint" aria-hidden="true" />
                          ) : (
                            <ChevronRight className="h-4 w-4 shrink-0 text-faint transition-transform duration-200 group-hover:translate-x-0.5" aria-hidden="true" />
                          )}
                        </button>
                        {open && total > 0 && (
                          <ol className="mb-2 space-y-0.5 rounded-lg bg-soft/50 px-2.5 py-2">
                            {course.chapters.map((chapter, idx) => (
                              <li key={chapter.id} className="flex items-center gap-3 rounded px-2 py-1.5 text-[13px] hover:bg-white">
                                <span className="w-5 shrink-0 font-mono text-[11px] text-faint tnum">{idx + 1}.</span>
                                <span className="flex-1 min-w-0 truncate text-body">{chapter.title}</span>
                                <span className={`flex shrink-0 items-center gap-1 text-xs ${resultColor(chapterState(chapter))}`}>
                                  {getCourseStatusIcon(chapterState(chapter))}
                                  {resultLabels[chapterState(chapter)] || '等待中'}
                                </span>
                              </li>
                            ))}
                          </ol>
                        )}
                      </div>
                    );
                  })}
                </div>
              </section>
            )}

            {/* 执行日志 */}
            <section className="rounded-xl border border-line bg-white shadow-card animate-stagger-up" style={{ animationDelay: '300ms' }}>
              <div className="flex items-center gap-2 border-b border-line px-5 py-3.5">
                <FileText className="h-4 w-4 text-brand" aria-hidden="true" />
                <h2 className="text-[15px] font-semibold">执行日志</h2>
                <span className="ml-auto text-xs text-faint">最近 {LOG_LIMIT} 条</span>
              </div>
              <div className="p-4">
                {logsTruncated && <p className="mb-2 text-xs text-faint">较早的日志已省略，仅保留最近 {LOG_LIMIT} 条。</p>}
                <div role="log" aria-label="执行日志" aria-live="off" className="h-[clamp(22.5rem,48vh,42rem)] overflow-y-auto rounded-lg bg-gray-900 p-4 font-mono text-xs leading-relaxed scroll-brutal">
                  {logs.length === 0 ? (
                    <p className="text-gray-500">等待日志输出...</p>
                  ) : (
                    logs.map((log) => (
                      <div key={log.seq} className="mb-0.5">
                        <span className="mr-2 text-gray-500 tnum">
                          [{new Date(log.timestamp * 1000).toLocaleTimeString('zh-CN')}]
                        </span>
                        <span className={getLogLevelColor(log.level || 'info')}>{log.message}</span>
                      </div>
                    ))
                  )}
                  <div ref={logsEndRef} />
                </div>
              </div>
            </section>
          </div>

          {/* 侧栏 */}
          <aside className="space-y-6 lg:sticky lg:top-24 animate-stagger-up" style={{ animationDelay: '200ms' }}>
            {/* 任务信息 */}
            <section className="rounded-xl border border-line bg-white shadow-card">
              <div className="border-b border-line px-5 py-3.5">
                <h2 className="text-[15px] font-semibold">任务信息</h2>
              </div>
              <dl className="space-y-4 p-5">
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
              </dl>
            </section>

            {finished && !missing && (
              <div className="flex items-start gap-2.5 rounded-xl border border-success/25 bg-success/5 p-4 animate-fade-in" role="status">
                <CheckCircle2 className="mt-0.5 h-4.5 w-4.5 shrink-0 text-success" aria-hidden="true" />
                <div>
                  <p className="text-sm font-semibold text-success">所有任务已完成</p>
                  <p className="mt-0.5 text-xs text-body">全部课程已按配置学习完毕</p>
                </div>
              </div>
            )}

            {partial && !missing && (
              <div className="flex items-start gap-2.5 rounded-xl border border-warning/25 bg-warning/5 p-4 animate-fade-in" role="status">
                <AlertCircle className="mt-0.5 h-4.5 w-4.5 shrink-0 text-warning" aria-hidden="true" />
                <div>
                  <p className="text-sm font-semibold text-warning">任务已结束，部分内容未完成</p>
                  <p className="mt-0.5 text-xs text-body">部分章节失败或被跳过，请查看课程明细和日志</p>
                </div>
              </div>
            )}

            {errored && !missing && (
              <div className="flex items-start gap-2.5 rounded-xl border border-danger/25 bg-danger/5 p-4 animate-fade-in" role="status">
                <AlertCircle className="mt-0.5 h-4.5 w-4.5 shrink-0 text-danger" aria-hidden="true" />
                <div>
                  <p className="text-sm font-semibold text-danger">任务执行失败</p>
                  <p className="mt-0.5 text-xs text-body">请查看错误信息或日志排查原因</p>
                </div>
              </div>
            )}

            {taskStatus?.stats && (
              <section className="rounded-xl border border-line bg-white shadow-card">
                <div className="border-b border-line px-5 py-3.5">
                  <h2 className="text-[15px] font-semibold">详细统计</h2>
                </div>
                <dl className="divide-y divide-line px-5">
                  {[
                    ['总章节数', taskStatus.stats.total_chapters || 0, ''],
                    ['已完成章节（含无任务）', taskStatus.stats.completed_chapters || 0, 'text-success'],
                    ['无任务章节', taskStatus.stats.empty_chapters || 0, ''],
                    ['失败章节', taskStatus.stats.failed_chapters || 0, 'text-danger'],
                    ['跳过章节', taskStatus.stats.skipped_chapters || 0, 'text-warning'],
                    ['失败课程', taskStatus.stats.failed_courses || 0, 'text-danger'],
                    ['总任务数', taskStatus.stats.total_tasks || 0, ''],
                    ['已完成任务', taskStatus.stats.completed_tasks || 0, 'text-success'],
                    ...(taskStatus.stats.failed_tasks > 0
                      ? [['失败任务', taskStatus.stats.failed_tasks, 'text-danger']]
                      : []),
                    ...(taskStatus.stats.skipped_tasks > 0
                      ? [['跳过任务', taskStatus.stats.skipped_tasks, 'text-warning']]
                      : []),
                  ].map(([label, value, color]) => (
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

            <Button variant="outline" className="w-full" onClick={onBack}>
              <ArrowLeft className="h-4 w-4" aria-hidden="true" />
              返回课程选择
            </Button>
          </aside>
        </div>
      </main>
    </div>
  );
};

export default StudyProgress;
