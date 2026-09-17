import React from 'react';
import Input from './ui/Input';
import Label from './ui/Label';

export const courseToolLabels = {
  study: '自动学习', visits: '学习次数', catalog: '资源读取',
  video_time: '视频时长', reading_time: '阅读时长', download: '资源下载',
};

export function validateVisits(options) {
  const count = Number(options.count);
  const interval = Number(options.interval);
  return {
    count: Number.isInteger(count) && count >= 1 && count <= 1000 ? '' : '次数须为 1–1000 的整数',
    interval: Number.isInteger(interval) && interval >= 1 && interval <= 3600 ? '' : '间隔须为 1–3600 秒的整数',
  };
}

export function validateMinutes(value) {
  const minutes = Number(value);
  return Number.isFinite(minutes) && minutes >= 0.1 && minutes <= 1440
    ? '' : '时长须在 0.1–1440 分钟之间';
}

const CourseToolSettings = ({ taskType, onTaskTypeChange, options, onOptionsChange, disabled = false }) => {
  const errors = validateVisits(options);
  return (
    <div className="space-y-5">
      <div className="space-y-1.5">
        <Label htmlFor="course-task-type">执行功能</Label>
        <select
          id="course-task-type"
          value={taskType}
          disabled={disabled}
          onChange={(event) => onTaskTypeChange(event.target.value)}
          className="h-10 w-full cursor-pointer rounded-lg border border-line bg-white px-3 text-sm text-ink focus:border-brand focus:outline-none focus:ring-4 focus:ring-brand/15 disabled:opacity-60"
        >
          {['study', 'visits', 'video_time', 'reading_time', 'download'].map((type) => (
            <option key={type} value={type}>{courseToolLabels[type]}</option>
          ))}
        </select>
      </div>

      {taskType === 'visits' && (
        <>
          {[
            ['count', '每门课程提交次数', 1000, '次'],
            ['interval', '提交间隔（秒）', 3600, '秒'],
          ].map(([field, label, max, unit]) => (
            <div key={field} className="space-y-1.5">
              <Label htmlFor={`visits-${field}`}>{label}</Label>
              <Input
                id={`visits-${field}`} type="number" min="1" max={max} step="1"
                value={options[field]} disabled={disabled}
                onChange={(event) => onOptionsChange({ ...options, [field]: event.target.value })}
                aria-invalid={!!errors[field]} aria-describedby={`visits-${field}-hint`}
              />
              <p id={`visits-${field}-hint`} role={errors[field] ? 'alert' : undefined} className={`text-xs ${errors[field] ? 'text-danger' : 'text-faint'}`}>
                {errors[field] || `范围 1–${max} ${unit}`}
              </p>
            </div>
          ))}
          <p className="text-xs leading-relaxed text-faint">逐次提交并显示执行结果。平台实际统计可能延迟更新，可在进度页查看前后次数。</p>
        </>
      )}

      {taskType === 'video_time' && (
        <p className="text-xs leading-relaxed text-faint">先读取所选课程的视频列表，再勾选视频并设置每个视频增加的时长。执行时可随时停止。</p>
      )}
      {taskType === 'reading_time' && (
        <p className="text-xs leading-relaxed text-faint">先读取课程的阅读任务，再勾选任务并设置新增时长。在应用后台读取关联书籍，无需新窗口或操作鼠标。平台当天统计可能次日更新。</p>
      )}
      {taskType === 'download' && (
        <p className="text-xs leading-relaxed text-faint">先读取所选课程的资源列表，再勾选需要下载的视频、音频或文件。完成后可打开下载目录。</p>
      )}
    </div>
  );
};

export default CourseToolSettings;
