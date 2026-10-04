import React, { useState, useId } from 'react';
import { FolderOpen, Loader2 } from 'lucide-react';
import Button from './ui/Button';
import Input from './ui/Input';
import Label from './ui/Label';
import { isTauriDesktop, desktopBridge } from '../lib/desktopBridge';
import { DEFAULT_DOWNLOAD_DIR, loadDownloadDir, saveDownloadDir } from '../lib/downloadDir';

export const courseToolLabels = {
  study: '自动学习', visits: '学习次数', catalog: '资源读取',
  video_time: '视频时长', reading_time: '阅读时长', download: '资源下载',
};

export const taskTypeOrder = ['study', 'visits', 'video_time', 'reading_time', 'download'];

// 决策顺序第一环：先选本次要做什么，再挑课程和参数。
const TaskTypePicker = ({ taskType, onTaskTypeChange, disabled = false }) => {
  const name = useId();
  return (
    <fieldset className="flex max-w-full flex-wrap gap-1 rounded-xl border border-line bg-white p-1 shadow-card" aria-label="执行功能">
      <legend className="sr-only">执行功能</legend>
      {taskTypeOrder.map((type) => (
        <label key={type} className="relative cursor-pointer">
          <input
            type="radio"
            name={name}
            value={type}
            checked={taskType === type}
            disabled={disabled}
            onChange={() => onTaskTypeChange(type)}
            className="peer sr-only"
          />
          <span className={`flex h-8 items-center rounded-lg px-3 text-[13px] font-medium transition-colors duration-150
            peer-checked:bg-brand peer-checked:text-white peer-checked:shadow-sm
            peer-focus-visible:outline-none peer-focus-visible:ring-4 peer-focus-visible:ring-brand/20
            peer-disabled:cursor-not-allowed peer-disabled:opacity-45
            text-body hover:bg-soft`}>
            {courseToolLabels[type]}
          </span>
        </label>
      ))}
    </fieldset>
  );
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

const CourseToolSettings = ({ taskType, options, onOptionsChange, disabled = false }) => {
  const errors = validateVisits(options);
  const [downloadDir, setDownloadDir] = useState(() => loadDownloadDir());
  const [picking, setPicking] = useState(false);
  const canPickFolder = isTauriDesktop();
  const handleDownloadDirChange = (event) => {
    setDownloadDir(event.target.value);
    saveDownloadDir(event.target.value);
  };
  const handlePickDownloadDir = async () => {
    if (!canPickFolder || picking) return;
    setPicking(true);
    try {
      const picked = await desktopBridge.pickDownloadDir(downloadDir);
      if (picked) {
        setDownloadDir(picked);
        saveDownloadDir(picked);
      }
    } catch {
      // 选择器不可用时保留手填路径。
    } finally {
      setPicking(false);
    }
  };
  return (
    <div className="space-y-5">
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
          <p className="text-[13px] leading-relaxed text-body">逐次提交并显示执行结果。平台实际统计可能延迟更新，可在进度页查看前后次数。</p>
        </>
      )}

      {taskType === 'video_time' && (
        <p className="text-[13px] leading-relaxed text-body">先读取所选课程的视频列表，再勾选视频并设置每个视频增加的时长。执行时可随时停止。</p>
      )}
      {taskType === 'reading_time' && (
        <p className="text-[13px] leading-relaxed text-body">先读取课程的阅读任务，再勾选任务并设置新增时长。执行时会打开浏览器窗口滚动阅读页，请不要关闭该窗口，也不会占用鼠标。平台当天统计可能次日更新。</p>
      )}
      {taskType === 'download' && (
        <>
          <div className="space-y-1.5">
            <Label htmlFor="download-dir">下载目录</Label>
            <div className="flex gap-2">
              <Input
                id="download-dir"
                type="text"
                value={downloadDir}
                disabled={disabled}
                onChange={handleDownloadDirChange}
                aria-describedby="download-dir-hint"
                spellCheck={false}
              />
              <Button
                type="button"
                variant="outline"
                className="shrink-0"
                onClick={handlePickDownloadDir}
                disabled={disabled || !canPickFolder || picking}
                title={canPickFolder ? '在文件管理器中选择文件夹' : '选择文件夹需在桌面版中使用'}
              >
                {picking ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <FolderOpen className="h-4 w-4" aria-hidden="true" />}
                {picking ? '打开中' : '选择'}
              </Button>
            </div>
            <p id="download-dir-hint" className="text-xs text-faint">
              文件将保存到此目录，默认 {DEFAULT_DOWNLOAD_DIR}；目录不存在时会自动创建。
            </p>
          </div>
          <p className="text-[13px] leading-relaxed text-body">先读取所选课程的资源列表，再勾选需要下载的视频、音频或文件。完成后可打开下载目录。</p>
        </>
      )}
    </div>
  );
};

export { TaskTypePicker };
export default CourseToolSettings;
