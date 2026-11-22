import { memo, useMemo } from 'react';
import {
  AlertCircle, BookOpen, CheckCircle2, ChevronDown, ChevronRight,
  Clock, FileText, Loader2, MonitorPlay, StopCircle, XCircle,
} from 'lucide-react';
import { chapterState, resultLabels } from '../lib/taskPolling';

export const formatTime = (seconds) => {
  if (!Number.isFinite(seconds) || seconds < 0) return '--:--';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
};

const resultColor = (state) => (
  state === 'error'
    ? 'text-danger'
    : ['skipped', 'partial'].includes(state)
      ? 'text-warning-ink'
      : state === 'completed'
        ? 'text-success-ink'
        : 'text-faint'
);

const getCourseStatusIcon = (status) => {
  switch (status) {
    case 'running':
      return <Loader2 className="h-4 w-4 text-brand animate-spin" aria-hidden="true" />;
    case 'completed':
      return <CheckCircle2 className="h-4 w-4 text-success-ink" aria-hidden="true" />;
    case 'empty':
      return <FileText className="h-4 w-4 text-faint" aria-hidden="true" />;
    case 'partial':
    case 'skipped':
      return <AlertCircle className="h-4 w-4 text-warning-ink" aria-hidden="true" />;
    case 'error':
      return <XCircle className="h-4 w-4 text-danger" aria-hidden="true" />;
    case 'cancelled':
      return <StopCircle className="h-4 w-4 text-faint" aria-hidden="true" />;
    default:
      return <Clock className="h-4 w-4 text-faint" aria-hidden="true" />;
  }
};

// 平台复核结论必须可读：按状态区分颜色，禁用近似背景色的 text-muted。
const getVerificationInfo = (verification) => {
  switch (verification.status) {
    case 'confirmed':
      return { text: '平台已确认完成', cls: 'text-success-ink', icon: CheckCircle2 };
    case 'pending':
      return { text: '平台仍有未完成任务', cls: 'text-warning-ink', icon: AlertCircle };
    case 'cancelled':
      return { text: '平台复核已停止', cls: 'text-faint', icon: StopCircle };
    default:
      return { text: '执行已结束，平台完成状态未确认', cls: 'text-warning-ink', icon: Clock };
  }
};

export const ActiveVideoJobs = memo(function ActiveVideoJobs({ activeJobs }) {
  if (!activeJobs.length) return null;
  return (
    <section className="rounded-xl border border-line bg-white p-5 shadow-card animate-stagger-up" style={{ animationDelay: '180ms' }}>
      <h2 className="mb-4 flex items-center gap-2 text-[15px] font-semibold">
        <MonitorPlay className="h-4 w-4 text-brand" aria-hidden="true" />
        正在播放视频
        <span className="rounded-full bg-brand-soft px-2 py-0.5 text-xs font-medium text-brand tnum">
          {activeJobs.length}
        </span>
      </h2>
      <div className="space-y-3">
        {activeJobs.map(([jobKey, job]) => {
          const pct = Number.isFinite(job?.progress) ? Math.max(0, Math.min(100, job.progress)) : 0;
          return (
            <div
              key={jobKey}
              className="rounded-lg border border-line p-4 transition-shadow duration-200 hover:shadow-card"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h3 className="truncate text-sm font-medium" title={job?.job_name}>
                    {job?.job_name || '未知任务'}
                  </h3>
                  <p className="mt-0.5 truncate text-xs text-faint" title={job?.course_name}>
                    {job?.course_name}
                  </p>
                </div>
                <span className="shrink-0 text-[15px] font-semibold text-brand tnum">
                  {Math.round(pct)}%
                </span>
              </div>
              <div className="mt-2.5">
                <div className="mb-1 flex items-center justify-between font-mono text-[11px] text-faint tnum">
                  <span>{formatTime(job?.current_time)}</span>
                  <span>{formatTime(job?.duration)}</span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-soft">
                  <div
                    className="h-full rounded-full bg-brand transition-[width] duration-700 ease-out"
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
});

export const CourseDetailsList = memo(function CourseDetailsList({ courses, expandedCourses, onToggleCourse }) {
  const courseDoneCounts = useMemo(() => {
    const counts = new Map();
    for (const course of courses || []) {
      counts.set(
        course.id,
        course.chapters ? course.chapters.filter((c) => ['completed', 'empty'].includes(chapterState(c))).length : 0,
      );
    }
    return counts;
  }, [courses]);

  if (!courses?.length) return null;
  return (
    <section className="rounded-xl border border-line bg-white shadow-card animate-stagger-up" style={{ animationDelay: '240ms' }}>
      <div className="flex items-center gap-2 border-b border-line px-5 py-3.5">
        <BookOpen className="h-4 w-4 text-brand" aria-hidden="true" />
        <h2 className="text-[15px] font-semibold">课程明细</h2>
      </div>
      <div className="divide-y divide-line px-2.5 py-2">
        {courses.map((course) => {
          const open = expandedCourses.has(course.id);
          const done = courseDoneCounts.get(course.id) ?? 0;
          const total = course.chapters?.length || 0;
          return (
            <div key={course.id}>
              {total > 0 ? (
                <button
                  type="button"
                  onClick={() => onToggleCourse(course.id)}
                  aria-expanded={open}
                  className="group flex w-full items-center gap-3 rounded-lg px-2.5 py-3 text-left transition-colors duration-150 hover:bg-soft focus-visible:bg-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand"
                >
                  {getCourseStatusIcon(course.status)}
                  <span title={course.title} className="min-w-0 flex-1 break-words text-sm font-medium">{course.title}</span>
                  <span className={`shrink-0 text-xs ${resultColor(course.status)}`}>{resultLabels[course.status] || '等待中'}</span>
                  <span className="shrink-0 rounded-full bg-soft px-2 py-0.5 text-xs text-faint tnum">
                    {done} / {total} 章节
                  </span>
                  {open ? (
                    <ChevronDown className="h-4 w-4 shrink-0 text-faint" aria-hidden="true" />
                  ) : (
                    <ChevronRight className="h-4 w-4 shrink-0 text-faint transition-transform duration-200 group-hover:translate-x-0.5" aria-hidden="true" />
                  )}
                </button>
              ) : (
                <div className="flex w-full items-center gap-3 rounded-lg px-2.5 py-3 text-left">
                  {getCourseStatusIcon(course.status)}
                  <span title={course.title} className="min-w-0 flex-1 break-words text-sm font-medium">{course.title}</span>
                  <span className={`shrink-0 text-xs ${resultColor(course.status)}`}>{resultLabels[course.status] || '等待中'}</span>
                </div>
              )}
              {course.verification && (() => {
                const info = getVerificationInfo(course.verification);
                return (
                  <p className={`flex items-start gap-1.5 px-2.5 pb-2 text-xs ${info.cls}`} role="status">
                    <info.icon className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                    <span className="min-w-0 break-words">
                      {info.text}{course.verification.reason && `：${course.verification.reason}`}
                    </span>
                  </p>
                );
              })()}
              {open && total > 0 && (
                <ol className="mb-2 space-y-0.5 rounded-lg bg-soft/50 px-2.5 py-2">
                  {course.chapters.map((chapter, idx) => {
                    const state = chapterState(chapter);
                    return (
                      <li key={chapter.id} className="flex items-center gap-3 rounded px-2 py-1.5 text-[13px] hover:bg-white">
                        <span className="w-5 shrink-0 font-mono text-[11px] text-faint tnum">{idx + 1}.</span>
                        <span className="min-w-0 flex-1 break-words text-body">{chapter.title}</span>
                        <span className={`flex shrink-0 items-center gap-1 text-xs ${resultColor(state)}`}>
                          {getCourseStatusIcon(state)}
                          {resultLabels[state] || '等待中'}
                        </span>
                      </li>
                    );
                  })}
                </ol>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
});
