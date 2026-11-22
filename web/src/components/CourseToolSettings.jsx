import { useState, useId, useMemo } from 'react';
import { FolderOpen, Loader2 } from 'lucide-react';
import Button from './ui/Button';
import Input from './ui/Input';
import Label from './ui/Label';
import { cn } from '../lib/utils';
import { isTauriDesktop, desktopBridge } from '../lib/desktopBridge';
import { DEFAULT_DOWNLOAD_DIR, loadDownloadDir, saveDownloadDir } from '../lib/downloadDir';

const numberInputCls = '[appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none';
const invalidInputCls = 'border-danger focus:border-danger focus:ring-danger/15';

export const courseToolLabels = {
  study: '自动学习', visits: '学习次数', catalog: '资源读取',
  video_time: '视频时长', read_time: '阅读时长', download: '资源下载',
};

const taskTypeOrder = ['study', 'visits', 'video_time', 'read_time', 'download'];
const readHostPattern = /^mooc\d+(?:-\d+|-ans)?\.chaoxing\.com$/i;
const readPathPattern = /^\/(?:mooc-ans\/)?mycourse\/studentstudy$/;
const numericIdPattern = /^\d{1,20}$/;
const maxReadUrls = 50;

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

export function parseReadUrls(text) {
  if (typeof text !== 'string') return [];
  const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  return [...new Set(lines)];
}

export function isValidStudentStudyUrl(raw) {
  if (typeof raw !== 'string' || !raw.trim()) return false;
  let parsed;
  try {
    parsed = new URL(raw.trim());
  } catch {
    return false;
  }
  if (!['https:', 'http:'].includes(parsed.protocol)) return false;
  if (parsed.username || parsed.password || parsed.hash || (parsed.port && parsed.port !== '443')) return false;
  if (!readHostPattern.test(parsed.hostname) || !readPathPattern.test(parsed.pathname)) return false;
  const params = new Map();
  for (const [key, value] of parsed.searchParams.entries()) {
    const lower = key.toLowerCase();
    if (!params.has(lower)) params.set(lower, value.trim());
  }
  return numericIdPattern.test(params.get('courseid') || '')
    && numericIdPattern.test(params.get('clazzid') || '')
    && numericIdPattern.test(params.get('chapterid') || '');
}

export function validateReadUrlOptions(options) {
  const urls = parseReadUrls(options?.readUrls ?? '');
  let urlsError = '';
  if (!urls.length) {
    urlsError = '请粘贴至少一条有效的超星章节链接';
  } else if (urls.length > maxReadUrls) {
    urlsError = `单次最多支持 ${maxReadUrls} 条章节链接`;
  } else if (urls.some((item) => !isValidStudentStudyUrl(item))) {
    urlsError = '存在无效链接，需为超星 studentstudy 链接并包含 courseId、clazzid、chapterId';
  }
  return {
    urls: urlsError,
    minutes: validateMinutes(options?.readMinutes ?? ''),
  };
}

const CourseToolSettings = ({ taskType, options, onOptionsChange, disabled = false }) => {
  const errors = useMemo(
    () => (taskType === 'visits' ? validateVisits(options) : { count: '', interval: '' }),
    [taskType, options],
  );
  const readMode = options.readMode === 'url' ? 'url' : 'catalog';
  const readUrlErrors = useMemo(
    () => (taskType === 'read_time' && readMode === 'url' ? validateReadUrlOptions(options) : { urls: '', minutes: '' }),
    [taskType, readMode, options],
  );
  const readModeName = useId();
  const [downloadDir, setDownloadDir] = useState(() => loadDownloadDir());
  const [picking, setPicking] = useState(false);
  const canPickFolder = isTauriDesktop();
  // 输入过程只更新本地状态，失焦时才写 localStorage，避免逐键同步落盘。
  const handleDownloadDirChange = (event) => {
    setDownloadDir(event.target.value);
  };
  const handleDownloadDirBlur = () => {
    const saved = saveDownloadDir(downloadDir);
    setDownloadDir(saved || DEFAULT_DOWNLOAD_DIR);
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
                id={`visits-${field}`} type="number" inputMode="numeric" min="1" max={max} step="1"
                value={options[field]} disabled={disabled}
                onChange={(event) => onOptionsChange({ ...options, [field]: event.target.value })}
                aria-invalid={!!errors[field]} aria-describedby={`visits-${field}-hint`}
                className={cn(numberInputCls, errors[field] && invalidInputCls)}
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
      {taskType === 'read_time' && (
        <>
          <fieldset className="space-y-1.5" aria-label="阅读时长来源">
            <legend className="text-[13px] font-medium text-ink">目标来源</legend>
            <div className="grid grid-cols-2 gap-1 rounded-lg border border-line bg-soft/60 p-1">
              {[
                ['catalog', '按课程读取'],
                ['url', '按章节链接'],
              ].map(([value, label]) => (
                <label key={value} className="relative cursor-pointer">
                  <input
                    type="radio"
                    name={readModeName}
                    value={value}
                    checked={readMode === value}
                    disabled={disabled}
                    onChange={() => onOptionsChange({ ...options, readMode: value })}
                    className="peer sr-only"
                  />
                  <span className="flex h-8 items-center justify-center rounded-md px-2 text-[13px] font-medium text-body transition-colors duration-150 hover:bg-white/70 peer-checked:bg-white peer-checked:text-brand peer-checked:shadow-sm peer-focus-visible:outline-none peer-focus-visible:ring-2 peer-focus-visible:ring-brand/30 peer-disabled:cursor-not-allowed peer-disabled:opacity-45">
                    {label}
                  </span>
                </label>
              ))}
            </div>
          </fieldset>

          {readMode === 'catalog' ? (
            <p className="text-[13px] leading-relaxed text-body">
              先读取所选课程的阅读任务与文档列表，再勾选条目并设置每项增加的阅读时长。按实际时间每 30 秒上报一次心跳，执行时可随时停止。
            </p>
          ) : (
            <>
              <div className="space-y-1.5">
                <Label htmlFor="read-urls">章节链接（有多条时每行一条）</Label>
                <textarea
                  id="read-urls"
                  rows={4}
                  value={options.readUrls ?? ''}
                  disabled={disabled}
                  onChange={(event) => onOptionsChange({ ...options, readUrls: event.target.value })}
                  placeholder="https://mooc1.chaoxing.com/mycourse/studentstudy?chapterId=...&courseId=...&clazzid=..."
                  aria-invalid={!!(options.readUrls?.trim() && readUrlErrors.urls)}
                  aria-describedby="read-urls-hint"
                  spellCheck={false}
                  className="w-full rounded-lg border border-line bg-white px-3 py-2 font-mono text-xs leading-relaxed text-ink placeholder:text-faint focus:border-brand focus:outline-none focus:ring-4 focus:ring-brand/15 disabled:cursor-not-allowed disabled:bg-soft"
                />
                <p
                  id="read-urls-hint"
                  role={options.readUrls?.trim() && readUrlErrors.urls ? 'alert' : undefined}
                  className={`text-xs ${options.readUrls?.trim() && readUrlErrors.urls ? 'text-danger' : 'text-faint'}`}
                >
                  {(options.readUrls?.trim() && readUrlErrors.urls) || '支持 mooc1 / mooc1-2 等 studentstudy 章节链接（可自动识别拓展阅读与文档），无需先勾选课程。'}
                </p>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="read-url-minutes">每个链接增加阅读时长（分钟）</Label>
                <Input
                  id="read-url-minutes"
                  type="number"
                  inputMode="decimal"
                  min="0.1"
                  max="1440"
                  step="any"
                  value={options.readMinutes ?? '30'}
                  disabled={disabled}
                  onChange={(event) => onOptionsChange({ ...options, readMinutes: event.target.value })}
                  aria-invalid={!!readUrlErrors.minutes}
                  aria-describedby="read-url-minutes-hint"
                  className={cn(numberInputCls, readUrlErrors.minutes && invalidInputCls)}
                />
                <p
                  id="read-url-minutes-hint"
                  role={readUrlErrors.minutes ? 'alert' : undefined}
                  className={`text-xs ${readUrlErrors.minutes ? 'text-danger' : 'text-faint'}`}
                >
                  {readUrlErrors.minutes || '范围 0.1–1440 分钟。按实际时间运行，平台阅读统计可能次日更新。'}
                </p>
              </div>
            </>
          )}
        </>
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
                onBlur={handleDownloadDirBlur}
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
