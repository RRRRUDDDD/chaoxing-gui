import { useEffect, useState } from 'react';
import Button from '../ui/Button';
import {
  X, Check, Copy, AlertTriangle, CheckCircle2, Info, Wrench, XCircle, ExternalLink, Loader2, Square,
} from 'lucide-react';

export const QUESTION_TYPE_OPTIONS = [
  { value: 'single', label: '单选题' },
  { value: 'multiple', label: '多选题' },
  { value: 'judgement', label: '判断题' },
  { value: 'completion', label: '填空题' },
  { value: 'short', label: '简答题' },
];

export const QUESTION_TYPE_META = {
  single: { label: '单选题', cls: 'bg-brand-soft text-brand border-brand/15' },
  multiple: { label: '多选题', cls: 'bg-indigo-50 text-indigo-700 border-indigo-200/70' },
  judgement: { label: '判断题', cls: 'bg-emerald-50 text-emerald-700 border-emerald-200/70' },
  completion: { label: '填空题', cls: 'bg-amber-50 text-amber-800 border-amber-200/70' },
  short: { label: '简答题', cls: 'bg-slate-100 text-slate-700 border-slate-200/80' },
};

export function getQuestionTypeMeta(type) {
  if (!type) return { label: '通用题', cls: 'bg-soft text-body border-line' };
  const key = String(type).trim().toLowerCase();
  return QUESTION_TYPE_META[key] || { label: type, cls: 'bg-soft text-body border-line' };
}

export const MASTERY_LABELS = ['未学习', '生疏', '熟悉', '已掌握'];

export function MasteryDots({ level = 0, showLabel = true }) {
  const safe = Math.max(0, Math.min(3, Number(level) || 0));
  const barColor =
    safe >= 3 ? 'bg-success' : safe === 2 ? 'bg-brand' : safe === 1 ? 'bg-warning' : 'bg-line';
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] text-faint" title={`掌握度 ${safe}/3 (${MASTERY_LABELS[safe]})`}>
      <span className="inline-flex items-center gap-0.5" aria-hidden="true">
        {[1, 2, 3].map((idx) => (
          <span
            key={idx}
            className={`h-1.5 w-3 rounded-full transition-colors ${idx <= safe ? barColor : 'bg-line'}`}
          />
        ))}
      </span>
      {showLabel && <span className="tnum">{MASTERY_LABELS[safe]}</span>}
    </span>
  );
}

export function ToggleSwitch({ checked, onChange, disabled = false, id, label, description }) {
  return (
    <label
      htmlFor={id}
      className={`inline-flex items-center gap-2.5 select-none ${disabled ? 'cursor-not-allowed opacity-55' : 'cursor-pointer'}`}
    >
      <span className="relative inline-flex shrink-0 items-center">
        <input
          id={id}
          type="checkbox"
          className="peer sr-only"
          checked={Boolean(checked)}
          disabled={disabled}
          onChange={(e) => onChange?.(e.target.checked)}
        />
        <span className="h-5 w-9 rounded-full bg-line transition-colors peer-checked:bg-brand peer-focus-visible:ring-4 peer-focus-visible:ring-brand/20" />
        <span className="pointer-events-none absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform peer-checked:translate-x-4" />
      </span>
      {(label || description) && (
        <span className="flex flex-col">
          {label && <span className="text-xs font-medium text-ink">{label}</span>}
          {description && <span className="text-[11px] text-faint">{description}</span>}
        </span>
      )}
    </label>
  );
}

export function CopyButton({ text, label = '复制', copiedLabel = '已复制', className = '' }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async (e) => {
    e?.stopPropagation?.();
    if (!text) return;
    try {
      await navigator.clipboard.writeText(String(text));
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      // Fallback for non-secure clipboard context
      const ta = document.createElement('textarea');
      ta.value = String(text);
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try {
        document.execCommand('copy');
        setCopied(true);
        setTimeout(() => setCopied(false), 1600);
      } finally {
        document.body.removeChild(ta);
      }
    }
  };

  return (
    <button
      type="button"
      onClick={handleCopy}
      className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium transition ${
        copied
          ? 'bg-success/10 text-success-ink'
          : 'text-faint hover:bg-soft hover:text-ink'
      } ${className}`}
    >
      {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
      <span>{copied ? copiedLabel : label}</span>
    </button>
  );
}

export function ModalDialog({ open, onClose, title, subtitle, maxWidth = 'max-w-xl', children }) {
  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (e) => {
      if (e.key === 'Escape') onClose?.();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/45 p-4 backdrop-blur-sm animate-fade-in"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose?.();
      }}
      role="dialog"
      aria-modal="true"
    >
      <div className={`max-h-[90vh] w-full ${maxWidth} flex flex-col overflow-hidden rounded-2xl border border-line bg-white shadow-lift animate-pop-in`}>
        <div className="flex items-start justify-between gap-3 border-b border-line px-5 py-4">
          <div className="min-w-0">
            <h3 className="text-base font-semibold text-ink">{title}</h3>
            {subtitle && <p className="mt-0.5 text-xs text-faint">{subtitle}</p>}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭弹窗"
            className="rounded-lg p-1.5 text-faint transition hover:bg-soft hover:text-ink"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="overflow-y-auto px-5 py-4">{children}</div>
      </div>
    </div>
  );
}

export function ConfirmDialog({
  open,
  title = '确认操作',
  description = '',
  confirmText = '确认',
  cancelText = '取消',
  destructive = false,
  loading = false,
  extraOption = null,
  onConfirm,
  onCancel,
}) {
  return (
    <ModalDialog open={open} onClose={loading ? undefined : onCancel} title={title} maxWidth="max-w-md">
      <div className="space-y-4">
        {description && <p className="text-sm leading-relaxed text-body">{description}</p>}
        {extraOption}
        <div className="flex justify-end gap-2 pt-2">
          <Button type="button" variant="outline" size="sm" onClick={onCancel} disabled={loading}>
            {cancelText}
          </Button>
          <Button
            type="button"
            variant={destructive ? 'destructive' : 'default'}
            size="sm"
            onClick={onConfirm}
            disabled={loading}
          >
            {loading ? '处理中...' : confirmText}
          </Button>
        </div>
      </div>
    </ModalDialog>
  );
}

export function EmptyState({ icon: Icon, title, description, action }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-line bg-white/70 px-6 py-14 text-center">
      {Icon && (
        <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-soft text-faint">
          <Icon className="h-6 w-6" aria-hidden="true" />
        </div>
      )}
      <p className="text-sm font-semibold text-ink">{title}</p>
      {description && <p className="mt-1 max-w-md text-xs leading-relaxed text-faint">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ToastViewport({ toasts, onDismiss }) {
  if (!toasts?.length) return null;
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex max-w-sm flex-col gap-2">
      {toasts.map((t) => {
        const isErr = t.type === 'error';
        const isOk = t.type === 'success';
        const cls = isErr
          ? 'border-danger/30 bg-white text-danger shadow-lift'
          : isOk
            ? 'border-success/30 bg-white text-success-ink shadow-lift'
            : 'border-line bg-white text-ink shadow-lift';
        const Icon = isErr ? AlertTriangle : isOk ? CheckCircle2 : Info;
        return (
          <div
            key={t.id}
            role={isErr ? 'alert' : 'status'}
            className={`pointer-events-auto flex items-start gap-2.5 rounded-xl border px-3.5 py-2.5 text-xs animate-pop-in ${cls}`}
          >
            <Icon className="mt-0.5 h-4 w-4 shrink-0" />
            <span className="flex-1 leading-relaxed text-ink">{t.message}</span>
            <button
              type="button"
              onClick={() => onDismiss?.(t.id)}
              aria-label="关闭通知"
              className="rounded p-0.5 text-faint hover:bg-soft hover:text-ink"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
}

function renderInline(text) {
  const parts = [];
  const regex = /(`[^`]+`|\[[^\]]+\]\(https?:\/\/[^)\s]+\)|\*\*[^*]+\*\*|~~[^~]+~~|\*[^*]+\*)/g;
  let lastIdx = 0;
  let match;
  let key = 0;
  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIdx) {
      parts.push(text.slice(lastIdx, match.index));
    }
    const token = match[0];
    if (token.startsWith('`')) {
      parts.push(
        <code key={key++} className="rounded bg-soft px-1.5 py-0.5 font-mono text-[0.88em] text-brand-dark">
          {token.slice(1, -1)}
        </code>
      );
    } else if (token.startsWith('[')) {
      const linkMatch = token.match(/^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/);
      if (linkMatch) {
        parts.push(
          <a
            key={key++}
            href={linkMatch[2]}
            target="_blank"
            rel="noopener noreferrer"
            className="text-brand underline decoration-brand/40 underline-offset-2 hover:text-brand-dark"
          >
            {linkMatch[1]}
          </a>
        );
      } else {
        parts.push(token);
      }
    } else if (token.startsWith('**')) {
      parts.push(<strong key={key++} className="font-semibold text-ink">{token.slice(2, -2)}</strong>);
    } else if (token.startsWith('~~')) {
      parts.push(<del key={key++} className="text-faint line-through">{token.slice(2, -2)}</del>);
    } else {
      parts.push(<em key={key++} className="italic">{token.slice(1, -1)}</em>);
    }
    lastIdx = regex.lastIndex;
  }
  if (lastIdx < text.length) parts.push(text.slice(lastIdx));
  return parts;
}

function isTableSeparatorLine(line) {
  const trimmed = line.trim();
  if (!trimmed.includes('|') || !trimmed.includes('-')) return false;
  const cells = trimmed.replace(/^\|/, '').replace(/\|$/, '').split('|');
  return cells.length >= 2 && cells.every((c) => /^\s*:?-{3,}:?\s*$/.test(c));
}

function splitTableRow(line) {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim());
}

/** Lightweight, zero-dependency Markdown renderer for AI responses & explanations. */
export function MarkdownContent({ content, className = '' }) {
  const raw = String(content || '');
  if (!raw.trim()) return null;

  // Split by fenced code blocks first
  const segments = raw.split(/(```[\s\S]*?```)/g);

  return (
    <div className={`space-y-2 text-[13px] leading-relaxed text-body ${className}`}>
      {segments.map((seg, sIdx) => {
        if (seg.startsWith('```') && seg.endsWith('```')) {
          const inner = seg.slice(3, -3);
          const firstNl = inner.indexOf('\n');
          const lang = firstNl >= 0 ? inner.slice(0, firstNl).trim() : '';
          const code = firstNl >= 0 ? inner.slice(firstNl + 1) : inner;
          return (
            <div key={sIdx} className="overflow-hidden rounded-xl border border-line bg-slate-900 text-slate-100">
              <div className="flex items-center justify-between border-b border-slate-800 px-3 py-1.5 text-[11px] text-slate-400">
                <span className="font-mono">{lang || 'code'}</span>
                <CopyButton text={code} className="text-slate-300 hover:bg-slate-800 hover:text-white" />
              </div>
              <pre className="overflow-x-auto p-3 font-mono text-xs leading-relaxed">{code}</pre>
            </div>
          );
        }

        const lines = seg.split(/\r?\n/);
        const blocks = [];
        let listItems = [];
        let listType = null;

        const flushList = () => {
          if (!listItems.length) return;
          const Tag = listType === 'ol' ? 'ol' : 'ul';
          blocks.push(
            <Tag
              key={`list-${blocks.length}`}
              className={`space-y-1 pl-5 ${listType === 'ol' ? 'list-decimal' : 'list-disc'}`}
            >
              {listItems.map((item, i) => (
                <li key={i}>{renderInline(item)}</li>
              ))}
            </Tag>
          );
          listItems = [];
          listType = null;
        };

        for (let lIdx = 0; lIdx < lines.length; lIdx += 1) {
          const line = lines[lIdx];
          const trimmed = line.trim();
          if (!trimmed) {
            flushList();
            continue;
          }
          // Markdown table detection
          if (trimmed.includes('|') && lIdx + 1 < lines.length && isTableSeparatorLine(lines[lIdx + 1])) {
            flushList();
            const headers = splitTableRow(line);
            const rows = [];
            lIdx += 2;
            while (lIdx < lines.length && lines[lIdx].trim() && lines[lIdx].includes('|')) {
              rows.push(splitTableRow(lines[lIdx]));
              lIdx += 1;
            }
            lIdx -= 1;
            blocks.push(
              <div key={`tbl-${blocks.length}`} className="my-1.5 overflow-x-auto rounded-xl border border-line bg-white">
                <table className="w-full border-collapse text-left text-xs">
                  <thead>
                    <tr className="border-b border-line bg-canvas/80 text-ink">
                      {headers.map((h, hIdx) => (
                        <th key={hIdx} className="px-3 py-2 font-semibold whitespace-nowrap">
                          {renderInline(h)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line/60">
                    {rows.map((row, rIdx) => (
                      <tr key={rIdx} className="hover:bg-canvas/40">
                        {headers.map((_, cIdx) => (
                          <td key={cIdx} className="px-3 py-1.5 text-body align-top">
                            {renderInline(row[cIdx] ?? '')}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
            continue;
          }
          if (/^[-*_]{3,}$/.test(trimmed)) {
            flushList();
            blocks.push(<hr key={`hr-${lIdx}`} className="my-2 border-line" />);
            continue;
          }
          const hMatch = trimmed.match(/^(#{1,4})\s+(.+)$/);
          if (hMatch) {
            flushList();
            const level = hMatch[1].length;
            const cls =
              level === 1
                ? 'text-base font-semibold text-ink pt-1'
                : level === 2
                  ? 'text-sm font-semibold text-ink pt-1'
                  : 'text-[13px] font-semibold text-ink';
            blocks.push(<p key={`h-${lIdx}`} className={cls}>{renderInline(hMatch[2])}</p>);
            continue;
          }
          const ulMatch = trimmed.match(/^[-*•]\s+(.+)$/);
          if (ulMatch) {
            if (listType && listType !== 'ul') flushList();
            listType = 'ul';
            listItems.push(ulMatch[1]);
            continue;
          }
          const olMatch = trimmed.match(/^\d+[.)、]\s+(.+)$/);
          if (olMatch) {
            if (listType && listType !== 'ol') flushList();
            listType = 'ol';
            listItems.push(olMatch[1]);
            continue;
          }
          if (trimmed.startsWith('>')) {
            flushList();
            blocks.push(
              <blockquote key={`q-${lIdx}`} className="border-l-2 border-brand/40 bg-brand-soft/40 py-1 pl-3 pr-2 text-body">
                {renderInline(trimmed.replace(/^>\s*/, ''))}
              </blockquote>
            );
            continue;
          }
          flushList();
          blocks.push(<p key={`p-${lIdx}`}>{renderInline(line)}</p>);
        }
        flushList();

        return <div key={sIdx} className="space-y-1.5">{blocks}</div>;
      })}
    </div>
  );
}

export function buildChaoxingAgentPayload(chaoxingContext) {
  const cxUser = chaoxingContext?.userInfo;
  if (!cxUser?.username) return null;
  return {
    username: cxUser.username,
    password: cxUser.password || '',
    use_cookies: cxUser.use_cookies === true,
    activeTaskId: chaoxingContext?.activeTaskId || null,
    taskStatus: chaoxingContext?.taskStatus || null,
  };
}

export function AgentLiveProgressCard({ progress, enableTools = true, onCancel }) {
  const step = progress?.step || 1;
  const maxSteps = progress?.maxSteps || 8;
  const phase = progress?.phase || 'thinking';
  const activeTools = Array.isArray(progress?.activeTools) ? progress.activeTools : [];
  const completedTools = Array.isArray(progress?.toolCalls) ? progress.toolCalls : [];
  const elapsedSec = typeof progress?.elapsedMs === 'number'
    ? `${(progress.elapsedMs / 1000).toFixed(1)}s`
    : '';

  const phaseText = phase === 'running_tools' && activeTools.length > 0
    ? `正在执行工具：${activeTools.map((t) => t.label || t.name).join('、')}`
    : phase === 'summarizing'
      ? '工具执行完成，正在汇总分析结果...'
      : enableTools
        ? 'AI Agent 正在分析意图与规划工具...'
        : 'AI 助教正在结合你的学习上下文思考...';

  return (
    <div className="w-full max-w-md space-y-2 rounded-2xl border border-brand/25 bg-white px-3.5 py-3 text-xs shadow-xs animate-fade-in">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2 text-ink">
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-brand" />
          <span className="truncate font-medium">{phaseText}</span>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {enableTools && progress && (
            <span className="rounded-full bg-brand-soft px-2 py-0.5 text-[10px] font-medium text-brand tnum">
              第 {step}/{maxSteps} 步
            </span>
          )}
          {elapsedSec && (
            <span className="text-[10px] text-faint tnum">{elapsedSec}</span>
          )}
          {onCancel && (
            <button
              type="button"
              onClick={onCancel}
              className="inline-flex items-center gap-1 rounded-md border border-danger/30 bg-danger/5 px-2 py-0.5 text-[10px] font-medium text-danger transition hover:bg-danger hover:text-white"
              title="停止本次 Agent 生成"
            >
              <Square className="h-2.5 w-2.5 fill-current" />
              停止
            </button>
          )}
        </div>
      </div>

      {(completedTools.length > 0 || activeTools.length > 0) && (
        <div className="space-y-1 border-t border-line/70 pt-2">
          {completedTools.map((tc, idx) => (
            <div key={tc.id || idx} className="flex items-center justify-between gap-2 text-[11px]">
              <span className="flex min-w-0 items-center gap-1.5">
                {tc.ok ? (
                  <CheckCircle2 className="h-3 w-3 shrink-0 text-success" aria-hidden="true" />
                ) : (
                  <XCircle className="h-3 w-3 shrink-0 text-danger" aria-hidden="true" />
                )}
                <span className="shrink-0 font-medium text-ink">{tc.label || tc.name}</span>
                {tc.summary && <span className="truncate text-faint">· {tc.summary}</span>}
              </span>
              {typeof tc.durationMs === 'number' && (
                <span className="shrink-0 text-[10px] text-faint tnum">{tc.durationMs}ms</span>
              )}
            </div>
          ))}
          {activeTools.map((at, idx) => (
            <div key={at.id || `active-${idx}`} className="flex items-center gap-1.5 text-[11px] text-brand">
              <Loader2 className="h-3 w-3 shrink-0 animate-spin" aria-hidden="true" />
              <span className="font-medium">{at.label || at.name}</span>
              <span className="text-[10px] text-faint">执行中...</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function AgentToolCallsBlock({ toolCalls, onViewProgress, onNavigate }) {
  if (!Array.isArray(toolCalls) || toolCalls.length === 0) return null;

  const failedCount = toolCalls.filter((tc) => !tc.ok).length;
  const startedTask = toolCalls.find(
    (tc) => tc.ok && (
      tc.name === 'chaoxing_start_study'
      || tc.name === 'chaoxing_start_course_tool'
      || tc.name === 'chaoxing_resume_task'
    )
  );
  const bankMutated = toolCalls.some(
    (tc) => tc.ok && (tc.name === 'ai_save_question' || tc.name === 'ai_correct_question')
  );
  const graphTouched = toolCalls.some(
    (tc) => tc.ok && (tc.name === 'ai_review_knowledge_node' || tc.name === 'ai_get_knowledge_graph')
  );

  return (
    <div className="mb-2.5 space-y-1.5 rounded-xl border border-line bg-white p-2.5 text-xs">
      <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] font-semibold text-body">
        <span className="inline-flex items-center gap-1.5 text-brand">
          <Wrench className="h-3 w-3" aria-hidden="true" />
          Agent 已执行 {toolCalls.length} 项操作
          {failedCount > 0 && (
            <span className="rounded-full bg-danger/10 px-1.5 py-0.5 text-[10px] font-medium text-danger">
              {failedCount} 项失败
            </span>
          )}
        </span>
        <div className="flex flex-wrap items-center gap-1.5">
          {startedTask && onViewProgress && (
            <button
              type="button"
              onClick={() => onViewProgress()}
              className="inline-flex items-center gap-1 rounded-md bg-brand-soft px-2 py-0.5 text-[11px] font-medium text-brand transition hover:bg-brand hover:text-white"
            >
              <span>查看任务进度</span>
              <ExternalLink className="h-3 w-3" aria-hidden="true" />
            </button>
          )}
          {bankMutated && onNavigate && (
            <button
              type="button"
              onClick={() => onNavigate('questions')}
              className="inline-flex items-center gap-1 rounded-md bg-soft px-2 py-0.5 text-[11px] font-medium text-body transition hover:bg-brand-soft hover:text-brand"
            >
              <span>前往题库</span>
            </button>
          )}
          {graphTouched && onNavigate && (
            <button
              type="button"
              onClick={() => onNavigate('study')}
              className="inline-flex items-center gap-1 rounded-md bg-soft px-2 py-0.5 text-[11px] font-medium text-body transition hover:bg-brand-soft hover:text-brand"
            >
              <span>查看图谱</span>
            </button>
          )}
        </div>
      </div>

      <div className="space-y-1">
        {toolCalls.map((tc, idx) => (
          <details
            key={tc.id || idx}
            className="rounded-lg border border-line/80 bg-canvas/70 px-2.5 py-1.5"
          >
            <summary className="flex cursor-pointer items-center justify-between gap-2 text-[11px]">
              <span className="flex min-w-0 items-center gap-1.5">
                {tc.ok ? (
                  <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-success" aria-hidden="true" />
                ) : (
                  <XCircle className="h-3.5 w-3.5 shrink-0 text-danger" aria-hidden="true" />
                )}
                <span className="shrink-0 font-medium text-ink">{tc.label || tc.name}</span>
                {tc.summary && (
                  <span className="truncate text-faint">· {tc.summary}</span>
                )}
              </span>
              {typeof tc.durationMs === 'number' && (
                <span className="shrink-0 text-[10px] text-faint tnum">{tc.durationMs}ms</span>
              )}
            </summary>
            <div className="mt-2 space-y-1.5 border-t border-line/70 pt-2 text-[11px]">
              {tc.arguments && Object.keys(tc.arguments).length > 0 && (
                <div>
                  <p className="mb-0.5 text-[10px] font-medium text-faint">参数</p>
                  <pre className="max-h-28 overflow-auto rounded-md border border-line bg-white p-1.5 font-mono text-[10px] text-body">
                    {JSON.stringify(tc.arguments, null, 2)}
                  </pre>
                </div>
              )}
              {tc.resultPreview && (
                <div>
                  <p className="mb-0.5 text-[10px] font-medium text-faint">返回摘要</p>
                  <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-all rounded-md border border-line bg-white p-1.5 font-mono text-[10px] text-faint">
                    {tc.resultPreview}
                  </pre>
                </div>
              )}
            </div>
          </details>
        ))}
      </div>
    </div>
  );
}
