import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import Button from '../ui/Button';
import Select from '../ui/Select';
import { aiApi } from '../../api/aiApi';
import {
  MarkdownContent,
  CopyButton,
  AgentToolCallsBlock,
  AgentLiveProgressCard,
  buildChaoxingAgentPayload,
} from './aiShared';
import {
  X, Send, Sparkles, Database, AlertTriangle, BrainCircuit,
  BookmarkPlus, Maximize2, MessageSquarePlus, CornerDownLeft, Bot,
  RotateCcw, Square,
} from 'lucide-react';

function buildDrawerQuickActions(chaoxingContext) {
  const loggedIn = Boolean(chaoxingContext?.userInfo?.username);
  const taskRunning = Boolean(
    chaoxingContext?.activeTaskId
    && !['completed', 'partial', 'error', 'cancelled', 'missing'].includes(chaoxingContext?.taskStatus)
  );
  if (loggedIn && taskRunning) {
    return [
      { label: '查看当前刷课进度', prompt: '查看当前学习通刷课任务的执行进度、正在播放的视频任务和最新日志' },
      { label: '查看我的学习通课程', prompt: '帮我获取当前学习通账号的所有课程列表' },
      { label: '查看待复习知识点', prompt: '获取我的学习总览，列出到期或记忆留存较低的知识点' },
    ];
  }
  return [
    { label: '查看我的学习通课程', prompt: '帮我获取当前学习通账号的所有课程列表' },
    { label: '查看当前刷课进度', prompt: '查看当前学习通刷课任务的执行进度和最新日志' },
    { label: '检查刷课与题库设置', prompt: '读取我当前的学习通刷课默认参数和 AI 题库协同设置' },
  ];
}

export default function QuickTutorDrawer({
  open,
  request,
  config = null,
  chaoxingContext = null,
  onAgentEffects,
  onViewProgress,
  onNavigate,
  onClose,
  onOpenFullAgent,
  onStatsChanged,
  onToast,
}) {
  const [sessions, setSessions] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [subjects, setSubjects] = useState([]);
  const [subjectId, setSubjectId] = useState('');
  const [modelOverride, setModelOverride] = useState('');
  const [includeBank, setIncludeBank] = useState(true);
  const [includeMistakes, setIncludeMistakes] = useState(true);
  const [enableTools, setEnableTools] = useState(true);
  const [draft, setDraft] = useState('');
  const [pendingUserMsg, setPendingUserMsg] = useState('');
  const [liveProgress, setLiveProgress] = useState(null);
  const [sending, setSending] = useState(false);
  const [lastFailedInput, setLastFailedInput] = useState(null);
  const [errorMsg, setErrorMsg] = useState('');
  const [contextMeta, setContextMeta] = useState(null);

  const endRef = useRef(null);
  const textareaRef = useRef(null);
  const abortControllerRef = useRef(null);
  const [prevReqTs, setPrevReqTs] = useState(null);
  const handledAutoSendRef = useRef(null);

  const quickActions = useMemo(() => buildDrawerQuickActions(chaoxingContext), [chaoxingContext]);

  const availableModels = useMemo(() => {
    const out = [];
    for (const plat of config?.platforms || []) {
      if (plat.enabled === false) continue;
      if (!plat.apiKey && plat.id !== 'ollama') continue;
      for (const m of plat.models || []) {
        if (m.enabled === false) continue;
        out.push({ id: m.id, label: m.displayName || m.modelId });
      }
    }
    return out;
  }, [config]);

  useEffect(() => () => {
    abortControllerRef.current?.abort();
  }, []);

  // Sync incoming request metadata cleanly during render (no cascading effect setState)
  if (open && request?._ts && request._ts !== prevReqTs) {
    setPrevReqTs(request._ts);
    if (request.subjectId !== undefined) {
      setSubjectId(request.subjectId != null ? String(request.subjectId) : '');
    }
    if (request.includeBank !== undefined) setIncludeBank(Boolean(request.includeBank));
    if (request.includeMistakes !== undefined) setIncludeMistakes(Boolean(request.includeMistakes));
    setContextMeta(
      request.contextTitle || request.contextBadge
        ? { title: request.contextTitle || '', badge: request.contextBadge || '上下文已挂载' }
        : null
    );
    if (request.prompt && !request.autoSend) {
      setDraft(request.prompt);
    }
  }

  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    Promise.all([aiApi.listAgentSessions(), aiApi.listSubjects()])
      .then(([sessRes, subjRes]) => {
        if (cancelled) return;
        const fetched = sessRes.items || [];
        setSubjects(subjRes.items || []);
        setSessions((prev) => {
          if (!prev.length) return fetched;
          const map = new Map(fetched.map((s) => [s.id, s]));
          for (const s of prev) {
            const existing = map.get(s.id);
            if (!existing || (s.messages?.length || 0) >= (existing.messages?.length || 0)) {
              map.set(s.id, s);
            }
          }
          return [...map.values()];
        });
        setActiveId((prev) => prev || fetched[0]?.id || null);
      })
      .catch((err) => {
        if (!cancelled) setErrorMsg(err.message || '加载助教会话失败');
      });

    const onKeyDown = (e) => {
      if (e.key === 'Escape') onClose?.();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      cancelled = true;
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [open, onClose]);

  const handleStopGeneration = useCallback(() => {
    abortControllerRef.current?.abort();
  }, []);

  const sendMessage = useCallback(async (textToSend, overrides = {}) => {
    const regenerate = Boolean(overrides.regenerate);
    const text = regenerate ? String(textToSend || '').trim() : (textToSend ?? draft).trim();
    if ((!text && !regenerate) || sending) return;
    const useSessionId = overrides.sessionId !== undefined ? overrides.sessionId : (activeId || '');
    const useBank = overrides.includeBank !== undefined ? overrides.includeBank : includeBank;
    const useMistakes = overrides.includeMistakes !== undefined ? overrides.includeMistakes : includeMistakes;
    const useSubject = overrides.subjectId !== undefined ? overrides.subjectId : subjectId;
    const useTools = overrides.enableTools !== undefined ? overrides.enableTools : enableTools;

    const chaoxingPayload = buildChaoxingAgentPayload(chaoxingContext);
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setSending(true);
    setErrorMsg('');
    setLastFailedInput(null);
    setLiveProgress(null);
    if (!regenerate) {
      setPendingUserMsg(text);
      if (textToSend === undefined) setDraft('');
    }
    try {
      const res = await aiApi.chatWithAgent(
        {
          sessionId: useSessionId,
          message: text,
          includeBank: useBank,
          includeMistakes: useMistakes,
          subjectId: useSubject ? Number(useSubject) : null,
          enableTools: useTools,
          ...(modelOverride ? { modelId: modelOverride } : {}),
          ...(regenerate ? { regenerate: true } : {}),
          ...(chaoxingPayload ? { chaoxing: chaoxingPayload } : {}),
        },
        {
          signal: controller.signal,
          onProgress: (snap) => setLiveProgress(snap),
        }
      );
      const updated = res.session;
      setSessions((prev) => {
        const exists = prev.some((s) => s.id === updated.id);
        return exists ? prev.map((s) => (s.id === updated.id ? updated : s)) : [updated, ...prev];
      });
      setActiveId(updated.id);
      setPendingUserMsg('');
      setLiveProgress(null);
      if (Array.isArray(res.uiEffects) && res.uiEffects.length > 0) {
        onAgentEffects?.(res.uiEffects);
      }
      onStatsChanged?.();
    } catch (err) {
      setPendingUserMsg('');
      setLiveProgress(null);
      if (err?.cancelled || err?.name === 'AbortError' || err?.name === 'CanceledError') {
        if (!regenerate && textToSend === undefined) setDraft(text);
        onToast?.('已停止本次生成', 'info');
      } else {
        setErrorMsg(err.message || 'AI 助教响应失败');
        setLastFailedInput({ text, overrides });
        if (!regenerate && textToSend === undefined) setDraft(text);
      }
    } finally {
      if (abortControllerRef.current === controller) {
        abortControllerRef.current = null;
      }
      setSending(false);
    }
  }, [draft, sending, activeId, includeBank, includeMistakes, subjectId, enableTools, modelOverride, chaoxingContext, onAgentEffects, onStatsChanged, onToast]);

  // Trigger auto-send or focus composer when drawer opens with a request
  useEffect(() => {
    if (!open || !request?._ts || handledAutoSendRef.current === request._ts) return undefined;
    handledAutoSendRef.current = request._ts;

    if (request.prompt && request.autoSend) {
      const nextSubject = request.subjectId != null ? String(request.subjectId) : '';
      const t = setTimeout(() => {
        void sendMessage(request.prompt, {
          includeBank: request.includeBank !== undefined ? Boolean(request.includeBank) : includeBank,
          includeMistakes: request.includeMistakes !== undefined ? Boolean(request.includeMistakes) : includeMistakes,
          subjectId: request.subjectId !== undefined ? nextSubject : subjectId,
        });
      }, 0);
      return () => clearTimeout(t);
    }
    const t = setTimeout(() => textareaRef.current?.focus(), 80);
    return () => clearTimeout(t);
  }, [open, request, sendMessage, includeBank, includeMistakes, subjectId]);

  const activeSession = sessions.find((s) => s.id === activeId) || null;
  const visibleMessages = activeSession?.messages || [];

  useEffect(() => {
    if (open) {
      endRef.current?.scrollIntoView?.({ behavior: 'smooth' });
    }
  }, [open, visibleMessages.length, pendingUserMsg, liveProgress?.toolCalls?.length]);

  const handleNewSession = async () => {
    try {
      const created = await aiApi.createAgentSession('快捷辅导');
      setSessions((prev) => [created, ...prev]);
      setActiveId(created.id);
      setContextMeta(null);
      setErrorMsg('');
      setLastFailedInput(null);
    } catch (err) {
      setErrorMsg(err.message);
    }
  };

  const handleSaveToBank = async (userQ, assistantA) => {
    try {
      await aiApi.saveQuestion({
        question: userQ.slice(0, 300),
        answer: assistantA.slice(0, 1000),
        questionType: 'short',
        isAi: true,
        folderId: 0,
      });
      onStatsChanged?.();
      onToast?.('已将该条问答保存到本地题库（默认文件夹）', 'success');
    } catch (err) {
      setErrorMsg(err.message);
    }
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-40 flex justify-end bg-ink/30 backdrop-blur-[1px] animate-fade-in"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose?.();
      }}
      role="dialog"
      aria-modal="true"
      aria-label="快捷 AI 助教"
    >
      <div className="flex h-full w-full max-w-lg flex-col border-l border-line bg-white shadow-2xl">
        {/* 顶栏 */}
        <div className="flex items-center justify-between gap-2 border-b border-line px-4 py-3">
          <div className="flex min-w-0 items-center gap-2">
            <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-brand-soft text-brand">
              <Sparkles className="h-4 w-4" />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-1.5">
                <p className="truncate text-sm font-semibold text-ink">快捷 AI 助教</p>
                {enableTools && (
                  <span
                    className={`inline-flex shrink-0 items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium ${
                      chaoxingContext?.userInfo?.username
                        ? 'bg-success/10 text-success-ink'
                        : 'bg-soft text-faint'
                    }`}
                    title={
                      chaoxingContext?.userInfo?.username
                        ? `已绑定学习通账号 ${chaoxingContext.userInfo.username}，可直接指令操控刷课与课程`
                        : '未登录学习通时仅支持本地题库与答疑工具；登录后可直接操控学习通课程与刷课'
                    }
                  >
                    <span className={`h-1.5 w-1.5 rounded-full ${chaoxingContext?.userInfo?.username ? 'bg-success' : 'bg-faint/40'}`} />
                    {chaoxingContext?.userInfo?.username ? 'Agent 已连学习通' : 'Agent 未登录'}
                  </span>
                )}
              </div>
              <p className="truncate text-[11px] text-faint">答疑辅导 · 直接操控学习通课程、刷课、题库与图谱</p>
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              onClick={handleNewSession}
              title="新建快捷对话"
              className="rounded-lg p-1.5 text-faint transition hover:bg-soft hover:text-ink"
            >
              <MessageSquarePlus className="h-4 w-4" />
            </button>
            {onOpenFullAgent && (
              <button
                type="button"
                onClick={() => {
                  onClose?.();
                  onOpenFullAgent(activeId);
                }}
                title="在完整助教页打开"
                className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-medium text-body transition hover:bg-soft hover:text-brand"
              >
                <Maximize2 className="h-3.5 w-3.5" />
                <span>完整页</span>
              </button>
            )}
            <button
              type="button"
              onClick={onClose}
              aria-label="关闭快捷助教"
              className="rounded-lg p-1.5 text-faint transition hover:bg-soft hover:text-ink"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* 会话切换 + 上下文开关 */}
        <div className="space-y-2 border-b border-line bg-canvas/60 px-4 py-2.5 text-xs">
          {sessions.length > 0 && (
            <div className="flex items-center gap-2">
              <span className="shrink-0 text-[11px] text-faint">对话</span>
              <Select
                value={activeId || ''}
                onChange={(e) => setActiveId(e.target.value)}
                className="h-7 flex-1 text-xs"
              >
                {sessions.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.title} ({s.messages?.length || 0}条)
                  </option>
                ))}
              </Select>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-1.5">
            <button
              type="button"
              onClick={() => setEnableTools((v) => !v)}
              title="开启后 AI 可直接查询课程/章节进度、启动或停止学习通刷课任务、修改协同设置及检索保存题库"
              className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium transition ${
                enableTools
                  ? 'border-indigo-300 bg-indigo-50 text-indigo-700'
                  : 'border-line bg-white text-faint hover:text-ink'
              }`}
            >
              <Bot className="h-3 w-3" />
              Agent 操控
            </button>
            <button
              type="button"
              onClick={() => setIncludeBank((v) => !v)}
              className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium transition ${
                includeBank
                  ? 'border-brand/30 bg-brand-soft text-brand'
                  : 'border-line bg-white text-faint hover:text-ink'
              }`}
            >
              <Database className="h-3 w-3" />
              查题库
            </button>
            <button
              type="button"
              onClick={() => setIncludeMistakes((v) => !v)}
              className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium transition ${
                includeMistakes
                  ? 'border-warning/30 bg-warning/10 text-warning-ink'
                  : 'border-line bg-white text-faint hover:text-ink'
              }`}
            >
              <AlertTriangle className="h-3 w-3" />
              附错题
            </button>
            {availableModels.length > 1 && (
              <Select
                aria-label="快捷切换模型"
                value={modelOverride}
                onChange={(e) => setModelOverride(e.target.value)}
                className="h-6 w-28 text-[11px]"
              >
                <option value="">默认模型</option>
                {availableModels.map((m) => (
                  <option key={m.id} value={m.id}>{m.label}</option>
                ))}
              </Select>
            )}
            {subjects.length > 0 && (
              <div className="flex items-center gap-1">
                <BrainCircuit className="h-3 w-3 text-success-ink" />
                <Select
                  value={subjectId}
                  onChange={(e) => setSubjectId(e.target.value)}
                  className="h-6 w-28 text-[11px]"
                >
                  <option value="">不挂图谱</option>
                  {subjects.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </Select>
              </div>
            )}
          </div>

          {contextMeta && (
            <div className="flex items-center justify-between gap-2 rounded-lg border border-brand/20 bg-brand-soft/50 px-2.5 py-1.5 text-[11px]">
              <div className="min-w-0 flex-1 truncate text-brand">
                <span className="font-semibold">{contextMeta.badge}</span>
                {contextMeta.title && <span className="ml-1.5 text-body">{contextMeta.title}</span>}
              </div>
              <button
                type="button"
                onClick={() => setContextMeta(null)}
                className="shrink-0 text-faint hover:text-ink"
                aria-label="清除上下文提示"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          )}
        </div>

        {errorMsg && (
          <div role="alert" className="mx-4 mt-2.5 flex items-center justify-between gap-2 rounded-xl border border-danger/25 bg-danger/5 px-3 py-2 text-xs text-danger">
            <span className="truncate">{errorMsg}</span>
            <div className="flex shrink-0 items-center gap-1.5">
              {lastFailedInput && !sending && (
                <button
                  type="button"
                  onClick={() => void sendMessage(lastFailedInput.text, lastFailedInput.overrides)}
                  className="inline-flex items-center gap-1 rounded bg-danger/10 px-1.5 py-0.5 text-[11px] font-medium hover:bg-danger hover:text-white"
                >
                  <RotateCcw className="h-3 w-3" />
                  重试
                </button>
              )}
              <button type="button" onClick={() => setErrorMsg('')} className="shrink-0 underline">关闭</button>
            </div>
          </div>
        )}

        {/* 消息流 */}
        <div className="flex-1 space-y-3.5 overflow-y-auto px-4 py-4">
          {visibleMessages.length === 0 && !pendingUserMsg ? (
            <div className="flex h-full flex-col items-center justify-center space-y-3 py-8 text-center">
              <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-brand-soft text-brand">
                <Sparkles className="h-5 w-5" />
              </div>
              <div className="space-y-1">
                <p className="text-sm font-semibold text-ink">随时提问，或直接指令操控学习通</p>
                <p className="max-w-xs text-xs leading-relaxed text-faint">
                  支持讲题答疑，也可直接让 Agent 查课程章节、启动/停止/恢复刷课、修正错题或查看到期复习计划。
                </p>
              </div>
              {enableTools && (
                <div className="flex w-full max-w-xs flex-col gap-1.5 pt-1">
                  {quickActions.map((item) => (
                    <button
                      key={item.label}
                      type="button"
                      onClick={() => void sendMessage(item.prompt)}
                      className="rounded-xl border border-line bg-canvas/70 px-3 py-2 text-left text-xs font-medium text-body transition hover:border-brand/40 hover:bg-brand-soft/40 hover:text-brand"
                    >
                      {item.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <>
              {visibleMessages.map((msg, idx) => {
                const isUser = msg.role === 'user';
                const isLastAssistant = !isUser && idx === visibleMessages.length - 1;
                const prevUser = !isUser && idx > 0 && visibleMessages[idx - 1]?.role === 'user'
                  ? visibleMessages[idx - 1].content
                  : '';
                return (
                  <div key={idx} className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
                    <div
                      className={`max-w-[90%] rounded-2xl px-3.5 py-2.5 ${
                        isUser
                          ? 'bg-brand text-[13px] leading-relaxed text-white shadow-xs'
                          : 'border border-line bg-canvas/80 text-ink'
                      }`}
                    >
                      {!isUser && msg.toolCalls?.length > 0 && (
                        <AgentToolCallsBlock
                          toolCalls={msg.toolCalls}
                          onViewProgress={onViewProgress}
                          onNavigate={onNavigate}
                        />
                      )}
                      {!isUser && msg.reasoning && (
                        <details className="mb-2 rounded-lg border border-line bg-white px-2.5 py-1.5 text-xs text-faint">
                          <summary className="cursor-pointer font-medium text-body">查看深度思考过程</summary>
                          <p className="mt-1 whitespace-pre-wrap leading-relaxed">{msg.reasoning}</p>
                        </details>
                      )}
                      {isUser ? (
                        <p className="whitespace-pre-wrap">{msg.content}</p>
                      ) : (
                        <MarkdownContent content={msg.content} />
                      )}
                      {!isUser && (
                        <div className="mt-2 flex flex-wrap items-center justify-between gap-2 border-t border-line/70 pt-1.5 text-[11px] text-faint">
                          <span className="tnum">
                            {msg.modelName || 'AI 助教'} {msg.durationMs ? `· ${msg.durationMs}ms` : ''}
                          </span>
                          <div className="flex items-center gap-1">
                            <CopyButton text={msg.content} label="复制" />
                            {isLastAssistant && !sending && (
                              <button
                                type="button"
                                onClick={() => void sendMessage(prevUser, { regenerate: true })}
                                className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium text-faint hover:bg-soft hover:text-ink"
                                title="重新生成回复"
                              >
                                <RotateCcw className="h-3 w-3" />
                                重生成
                              </button>
                            )}
                            {prevUser && (
                              <button
                                type="button"
                                onClick={() => void handleSaveToBank(prevUser, msg.content)}
                                className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium text-brand hover:bg-brand-soft"
                              >
                                <BookmarkPlus className="h-3 w-3" />
                                存题库
                              </button>
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}

              {pendingUserMsg && (
                <div className="flex justify-end animate-fade-in">
                  <div className="max-w-[90%] rounded-2xl bg-brand px-3.5 py-2.5 text-[13px] leading-relaxed text-white shadow-xs">
                    <p className="whitespace-pre-wrap">{pendingUserMsg}</p>
                  </div>
                </div>
              )}
              {sending && (
                <div className="flex justify-start">
                  <AgentLiveProgressCard
                    progress={liveProgress}
                    enableTools={enableTools}
                    onCancel={handleStopGeneration}
                  />
                </div>
              )}
            </>
          )}
          <div ref={endRef} />
        </div>

        {/* 输入框 */}
        <form
          onSubmit={(e) => { e.preventDefault(); void sendMessage(); }}
          className="border-t border-line bg-white p-3"
        >
          <div className="flex items-end gap-2 rounded-xl border border-line bg-canvas/50 p-2 focus-within:border-brand focus-within:bg-white focus-within:ring-4 focus-within:ring-brand/15">
            <textarea
              ref={textareaRef}
              rows={2}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent?.isComposing) {
                  e.preventDefault();
                  void sendMessage();
                }
              }}
              placeholder={enableTools ? '发问或下达学习通指令（如：查看我的课程、1.5倍速刷高数）...' : '继续追问或粘贴题目（Enter 发送，Shift+Enter 换行）...'}
              disabled={sending}
              className="max-h-32 min-h-[44px] flex-1 resize-none bg-transparent px-1.5 py-1 text-xs leading-relaxed text-ink placeholder:text-faint focus:outline-none"
            />
            {sending ? (
              <Button type="button" variant="destructive" size="sm" onClick={handleStopGeneration} className="shrink-0" title="停止生成">
                <Square className="h-3 w-3 fill-current" />
              </Button>
            ) : (
              <Button type="submit" size="sm" disabled={!draft.trim()} className="shrink-0">
                <Send className="h-3.5 w-3.5" />
              </Button>
            )}
          </div>
          <p className="mt-1 flex items-center gap-1 px-1 text-[10px] text-faint">
            <CornerDownLeft className="h-3 w-3" />
            Enter 发送 · Esc 关闭抽屉
          </p>
        </form>
      </div>
    </div>
  );
}
