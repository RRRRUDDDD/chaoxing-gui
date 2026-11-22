import { useState, useEffect, useRef, useMemo } from 'react';
import Button from '../ui/Button';
import Select from '../ui/Select';
import { aiApi } from '../../api/aiApi';
import {
  MarkdownContent,
  CopyButton,
  ConfirmDialog,
  AgentToolCallsBlock,
  AgentLiveProgressCard,
  buildChaoxingAgentPayload,
} from './aiShared';
import {
  MessageSquarePlus, Trash2, Send, Sparkles, Database,
  AlertTriangle, BrainCircuit, BookmarkPlus, CornerDownLeft, Bot,
  RotateCcw, Pencil, Check, X, Search, Eraser, Cpu, Square,
} from 'lucide-react';

function buildQuickPrompts(chaoxingContext) {
  const loggedIn = Boolean(chaoxingContext?.userInfo?.username);
  const taskRunning = Boolean(
    chaoxingContext?.activeTaskId
    && !['completed', 'partial', 'error', 'cancelled', 'missing'].includes(chaoxingContext?.taskStatus)
  );
  if (loggedIn && taskRunning) {
    return [
      {
        title: '查看实时刷课进度',
        text: '查看当前学习通刷课任务的最新进度、正在播放的视频任务和最近日志',
      },
      {
        title: '诊断薄弱错题',
        text: '帮我统计刷题正确率、分析薄弱错题规律，并指出最容易丢分的知识点',
      },
      {
        title: '今日艾宾浩斯计划',
        text: '调用学习总览查看我当前到期与低留存知识点，制定今天的艾宾浩斯复习计划',
      },
    ];
  }
  if (loggedIn) {
    return [
      {
        title: '查看学习通课程与进度',
        text: '帮我列出当前学习通账号的全部课程，并查看当前是否有正在运行的刷课任务',
      },
      {
        title: '核对待修正错题',
        text: '帮我检索本地题库里的待修正错题，逐题核对答案并给出修正建议',
      },
      {
        title: '今日艾宾浩斯计划',
        text: '查看我的学习总览与知识图谱留存情况，制定今天的艾宾浩斯复习计划',
      },
    ];
  }
  return [
    {
      title: '诊断薄弱错题',
      text: '帮我分析当前题库的薄弱错题与待修正题目，总结最容易丢分的知识点',
    },
    {
      title: '今日艾宾浩斯计划',
      text: '查看我的学习总览和知识图谱留存率，制定今天的艾宾浩斯复习计划',
    },
    {
      title: '薄弱点模拟测验',
      text: '结合我的薄弱错题或学科图谱，给我出 3 道针对性练习题，每题附带答案和解析',
    },
  ];
}

export default function AgentChatTab({
  isActive = true,
  intent,
  config = null,
  dataVersion = 0,
  chaoxingContext = null,
  onAgentEffects,
  onViewProgress,
  onNavigate,
  onStatsChanged,
  onToast,
}) {
  const [sessions, setSessions] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [sessionQuery, setSessionQuery] = useState('');
  const [editingSessionId, setEditingSessionId] = useState(null);
  const [editingTitle, setEditingTitle] = useState('');
  const [subjects, setSubjects] = useState([]);
  const [subjectId, setSubjectId] = useState('');
  const [modelOverride, setModelOverride] = useState('');
  const [includeBank, setIncludeBank] = useState(true);
  const [includeMistakes, setIncludeMistakes] = useState(true);
  const [enableTools, setEnableTools] = useState(true);
  const [draft, setDraft] = useState(() => intent?.prefill || '');
  const [pendingUserMsg, setPendingUserMsg] = useState('');
  const [liveProgress, setLiveProgress] = useState(null);
  const [sending, setSending] = useState(false);
  const [lastFailedInput, setLastFailedInput] = useState(null);
  const [sessionToDelete, setSessionToDelete] = useState(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const endRef = useRef(null);
  const textareaRef = useRef(null);
  const abortControllerRef = useRef(null);
  const [prevIntentTs, setPrevIntentTs] = useState(intent?._ts || null);
  const handledEffectIntentRef = useRef(null);
  const syncedVerRef = useRef(dataVersion);
  const wasActiveRef = useRef(isActive);

  const availableModels = useMemo(() => {
    const out = [];
    for (const plat of config?.platforms || []) {
      if (plat.enabled === false) continue;
      if (!plat.apiKey && plat.id !== 'ollama') continue;
      for (const m of plat.models || []) {
        if (m.enabled === false) continue;
        out.push({
          id: m.id,
          label: `${m.displayName || m.modelId} (${plat.displayName || plat.name})`,
        });
      }
    }
    return out;
  }, [config]);

  const quickPrompts = useMemo(() => buildQuickPrompts(chaoxingContext), [chaoxingContext]);

  if (intent?._ts && intent._ts !== prevIntentTs) {
    setPrevIntentTs(intent._ts);
    if (intent.subjectId !== undefined) {
      setSubjectId(intent.subjectId ? String(intent.subjectId) : '');
    }
    if (intent.prefill) {
      setDraft(intent.prefill);
    }
  }

  useEffect(() => () => {
    abortControllerRef.current?.abort();
  }, []);

  useEffect(() => {
    if (!intent?._ts || handledEffectIntentRef.current === intent._ts) return undefined;
    handledEffectIntentRef.current = intent._ts;
    let cancelled = false;
    if (intent.sessionId) {
      const targetId = intent.sessionId;
      Promise.all([aiApi.listAgentSessions(), aiApi.listSubjects()])
        .then(([sessRes, subjRes]) => {
          if (cancelled) return;
          const list = sessRes.items || [];
          setSessions(list);
          setSubjects(subjRes.items || []);
          setActiveId((prev) => {
            if (list.some((s) => s.id === targetId)) return targetId;
            if (prev && list.some((s) => s.id === prev)) return prev;
            return list[0]?.id ?? null;
          });
        })
        .catch((err) => { if (!cancelled) setErrorMsg(err.message); });
    }
    let timer = null;
    if (intent.prefill) {
      timer = setTimeout(() => textareaRef.current?.focus(), 60);
    }
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [intent]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([aiApi.listAgentSessions(), aiApi.listSubjects()])
      .then(([sessRes, subjRes]) => {
        if (cancelled) return;
        const list = sessRes.items || [];
        setSessions(list);
        setSubjects(subjRes.items || []);
        setActiveId((prev) => (prev && list.some((s) => s.id === prev) ? prev : list[0]?.id ?? null));
      })
      .catch((err) => { if (!cancelled) setErrorMsg(err.message); });
    return () => { cancelled = true; };
  }, []);

  // Re-sync sessions softly when returning to Agent tab after chatting in QuickTutorDrawer
  useEffect(() => {
    const returnedToTab = isActive && !wasActiveRef.current;
    wasActiveRef.current = isActive;
    if (!returnedToTab || dataVersion === syncedVerRef.current) return undefined;
    syncedVerRef.current = dataVersion;
    let cancelled = false;
    Promise.all([aiApi.listAgentSessions(), aiApi.listSubjects()])
      .then(([sessRes, subjRes]) => {
        if (cancelled) return;
        const list = sessRes.items || [];
        setSessions(list);
        setSubjects(subjRes.items || []);
        setActiveId((prev) => (prev && list.some((s) => s.id === prev) ? prev : list[0]?.id ?? null));
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [isActive, dataVersion]);

  const activeSession = sessions.find((s) => s.id === activeId) || null;
  const visibleMessages = activeSession?.messages || [];

  const filteredSessions = useMemo(() => {
    const q = sessionQuery.trim().toLowerCase();
    if (!q) return sessions;
    return sessions.filter((s) => (s.title || '').toLowerCase().includes(q));
  }, [sessions, sessionQuery]);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ behavior: 'smooth' });
  }, [visibleMessages.length, pendingUserMsg, liveProgress?.toolCalls?.length]);

  // Auto-resize composer textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(160, Math.max(42, el.scrollHeight))}px`;
  }, [draft]);

  const handleNewSession = async () => {
    try {
      const created = await aiApi.createAgentSession('新对话');
      setSessions((prev) => [created, ...prev]);
      setActiveId(created.id);
      setErrorMsg('');
      setLastFailedInput(null);
    } catch (err) {
      setErrorMsg(err.message);
    }
  };

  const handleStartRename = (sess) => {
    setEditingSessionId(sess.id);
    setEditingTitle(sess.title || '');
  };

  const handleSaveRename = async () => {
    if (!editingSessionId) return;
    const cleaned = editingTitle.trim();
    if (!cleaned) {
      setEditingSessionId(null);
      return;
    }
    try {
      const updated = await aiApi.renameAgentSession(editingSessionId, cleaned);
      setSessions((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
      setEditingSessionId(null);
      onToast?.('对话标题已更新', 'success');
    } catch (err) {
      setErrorMsg(err.message);
    }
  };

  const handleConfirmClearSession = async () => {
    if (!activeId) return;
    try {
      const updated = await aiApi.clearAgentSession(activeId);
      setSessions((prev) => prev.map((s) => (s.id === updated.id ? updated : s)));
      setConfirmClear(false);
      onToast?.('当前对话记录已清空', 'info');
    } catch (err) {
      setErrorMsg(err.message);
    }
  };

  const handleConfirmDeleteSession = async () => {
    if (!sessionToDelete) return;
    try {
      const id = sessionToDelete.id;
      await aiApi.deleteAgentSession(id);
      setSessions((prev) => {
        const next = prev.filter((s) => s.id !== id);
        if (activeId === id) setActiveId(next[0]?.id ?? null);
        return next;
      });
      setSessionToDelete(null);
      onToast?.('对话已删除', 'info');
    } catch (err) {
      setErrorMsg(err.message);
    }
  };

  const handleStopGeneration = () => {
    abortControllerRef.current?.abort();
  };

  const sendMessage = async (textToSend, { regenerate = false } = {}) => {
    const text = regenerate ? String(textToSend || '').trim() : (textToSend ?? draft).trim();
    if ((!text && !regenerate) || sending) return;
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
          sessionId: activeId || '',
          message: text,
          includeBank,
          includeMistakes,
          subjectId: subjectId ? Number(subjectId) : null,
          enableTools,
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
        setLastFailedInput({ text, regenerate });
        if (!regenerate && textToSend === undefined) setDraft(text);
      }
    } finally {
      if (abortControllerRef.current === controller) {
        abortControllerRef.current = null;
      }
      setSending(false);
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

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent?.isComposing) {
      e.preventDefault();
      void sendMessage();
    }
  };

  return (
    <div className="space-y-3">
      {errorMsg && (
        <div role="alert" className="flex items-center justify-between gap-2 rounded-xl border border-danger/25 bg-danger/5 px-4 py-2.5 text-xs text-danger">
          <span className="min-w-0 flex-1">{errorMsg}</span>
          <div className="flex shrink-0 items-center gap-2">
            {lastFailedInput && !sending && (
              <button
                type="button"
                onClick={() => void sendMessage(lastFailedInput.text, { regenerate: lastFailedInput.regenerate })}
                className="inline-flex items-center gap-1 rounded-md bg-danger/10 px-2 py-0.5 font-medium text-danger hover:bg-danger hover:text-white"
              >
                <RotateCcw className="h-3 w-3" />
                重试
              </button>
            )}
            <button type="button" onClick={() => setErrorMsg('')} className="underline">关闭</button>
          </div>
        </div>
      )}

      <div className="grid gap-4 lg:h-[calc(100vh-8.5rem)] lg:min-h-[540px] lg:grid-cols-12">
        {/* 左侧会话列表 */}
        <aside className="flex flex-col rounded-2xl border border-line bg-white p-3.5 shadow-card lg:col-span-3 lg:overflow-hidden">
          <Button size="sm" className="w-full shrink-0" onClick={handleNewSession}>
            <MessageSquarePlus className="h-3.5 w-3.5" />
            新建助教对话
          </Button>
          {sessions.length > 3 && (
            <div className="relative mt-2 shrink-0">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-faint" />
              <input
                type="text"
                value={sessionQuery}
                onChange={(e) => setSessionQuery(e.target.value)}
                placeholder="筛选历史对话..."
                className="h-8 w-full rounded-lg border border-line bg-canvas/50 pl-8 pr-2.5 text-xs text-ink placeholder:text-faint focus:border-brand focus:bg-white focus:outline-none"
              />
            </div>
          )}
          <div className="mt-2.5 flex-1 space-y-1 overflow-y-auto pr-0.5">
            {filteredSessions.length === 0 ? (
              <p className="py-8 text-center text-xs text-faint">
                {sessions.length === 0 ? '暂无对话，发条消息即可开始' : '无匹配的历史对话'}
              </p>
            ) : (
              filteredSessions.map((s) => {
                const isEditing = editingSessionId === s.id;
                return (
                  <div
                    key={s.id}
                    className={`group flex items-center justify-between gap-1 rounded-xl px-2.5 py-2 text-xs transition ${
                      activeId === s.id ? 'bg-brand-soft font-medium text-brand' : 'text-body hover:bg-soft hover:text-ink'
                    }`}
                  >
                    {isEditing ? (
                      <div className="flex min-w-0 flex-1 items-center gap-1">
                        <input
                          type="text"
                          value={editingTitle}
                          onChange={(e) => setEditingTitle(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') void handleSaveRename();
                            if (e.key === 'Escape') setEditingSessionId(null);
                          }}
                          autoFocus
                          className="h-6 min-w-0 flex-1 rounded border border-brand bg-white px-1.5 text-xs text-ink focus:outline-none"
                        />
                        <button
                          type="button"
                          onClick={() => void handleSaveRename()}
                          title="保存标题"
                          className="rounded p-1 text-success hover:bg-white"
                        >
                          <Check className="h-3.5 w-3.5" />
                        </button>
                        <button
                          type="button"
                          onClick={() => setEditingSessionId(null)}
                          title="取消"
                          className="rounded p-1 text-faint hover:bg-white"
                        >
                          <X className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    ) : (
                      <>
                        <button
                          type="button"
                          onClick={() => setActiveId(s.id)}
                          className="min-w-0 flex-1 text-left"
                        >
                          <p className="truncate">{s.title}</p>
                          <p className="mt-0.5 text-[10px] text-faint tnum">
                            {(s.messages?.length || 0)} 条消息
                          </p>
                        </button>
                        <div className="hidden shrink-0 items-center gap-0.5 group-hover:inline-flex">
                          <button
                            type="button"
                            onClick={() => handleStartRename(s)}
                            title="重命名对话"
                            className="rounded p-1 text-faint hover:text-brand"
                          >
                            <Pencil className="h-3 w-3" />
                          </button>
                          <button
                            type="button"
                            onClick={() => setSessionToDelete(s)}
                            title="删除对话"
                            className="rounded p-1 text-faint hover:text-danger"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </aside>

        {/* 右侧对话主区 */}
        <div className="flex min-h-[520px] flex-col overflow-hidden rounded-2xl border border-line bg-white shadow-card lg:col-span-9 lg:min-h-0">
          {/* 上下文挂载工具条 */}
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line bg-canvas/50 px-4 py-2.5 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={() => setEnableTools((v) => !v)}
                title="开启后 AI 可直接查询课程/章节进度、启动或停止学习通刷课任务、修改协同设置、检索修正题库及管理知识图谱"
                className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-medium transition ${
                  enableTools
                    ? 'border-indigo-300 bg-indigo-50 text-indigo-700'
                    : 'border-line bg-white text-faint hover:text-ink'
                }`}
              >
                <Bot className="h-3 w-3" />
                学习通 Agent 操控
                {enableTools && (
                  <span className={`h-1.5 w-1.5 rounded-full ${chaoxingContext?.userInfo?.username ? 'bg-success' : 'bg-faint/40'}`} />
                )}
              </button>
              <button
                type="button"
                onClick={() => setIncludeBank((v) => !v)}
                className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-medium transition ${
                  includeBank
                    ? 'border-brand/30 bg-brand-soft text-brand'
                    : 'border-line bg-white text-faint hover:text-ink'
                }`}
              >
                <Database className="h-3 w-3" />
                检索本地题库
              </button>
              <button
                type="button"
                onClick={() => setIncludeMistakes((v) => !v)}
                className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 font-medium transition ${
                  includeMistakes
                    ? 'border-warning/30 bg-warning/10 text-warning-ink'
                    : 'border-line bg-white text-faint hover:text-ink'
                }`}
              >
                <AlertTriangle className="h-3 w-3" />
                附带薄弱错题
              </button>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              {availableModels.length > 0 && (
                <div className="flex items-center gap-1.5">
                  <Cpu className="h-3.5 w-3.5 text-brand" />
                  <Select
                    aria-label="选择助教模型"
                    value={modelOverride}
                    onChange={(e) => setModelOverride(e.target.value)}
                    className="h-8 w-44 text-xs"
                  >
                    <option value="">默认助教模型</option>
                    {availableModels.map((m) => (
                      <option key={m.id} value={m.id}>{m.label}</option>
                    ))}
                  </Select>
                </div>
              )}
              <div className="flex items-center gap-1.5">
                <BrainCircuit className="h-3.5 w-3.5 text-success-ink" />
                <Select value={subjectId} onChange={(e) => setSubjectId(e.target.value)} className="h-8 w-40 text-xs">
                  <option value="">不挂载知识图谱</option>
                  {subjects.map((s) => <option key={s.id} value={s.id}>图谱：{s.name}</option>)}
                </Select>
              </div>
              {activeSession && visibleMessages.length > 0 && !sending && (
                <button
                  type="button"
                  onClick={() => setConfirmClear(true)}
                  title="清空当前对话消息"
                  className="inline-flex h-8 items-center gap-1 rounded-lg border border-line bg-white px-2 text-[11px] text-faint transition hover:border-danger/30 hover:text-danger"
                >
                  <Eraser className="h-3.5 w-3.5" />
                  <span className="hidden xl:inline">清空</span>
                </button>
              )}
            </div>
          </div>

          {/* 消息流 */}
          <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
            {visibleMessages.length === 0 && !pendingUserMsg ? (
              <div className="flex h-full flex-col items-center justify-center space-y-4 py-6 text-center">
                <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-soft text-brand">
                  <Sparkles className="h-6 w-6" />
                </div>
                <div>
                  <p className="text-base font-semibold text-ink">AI 学习助教</p>
                  <p className="mt-1 max-w-md text-xs leading-relaxed text-faint">
                    既能结合本地题库、错题与艾宾浩斯知识图谱深度答疑，也可作为 Agent 直接查询学习通课程章节、启动/停止/恢复刷课、核对修正错题及记录复习进度。
                  </p>
                </div>
                <div className="grid w-full max-w-2xl gap-2.5 pt-2 sm:grid-cols-3">
                  {quickPrompts.map((item) => (
                    <button
                      key={item.title}
                      type="button"
                      onClick={() => void sendMessage(item.text)}
                      className="flex flex-col items-start rounded-xl border border-line bg-canvas/70 p-3 text-left transition hover:border-brand/40 hover:bg-brand-soft/30"
                    >
                      <span className="text-xs font-semibold text-ink">{item.title}</span>
                      <span className="mt-1 line-clamp-2 text-[11px] leading-relaxed text-faint">{item.text}</span>
                    </button>
                  ))}
                </div>
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
                        className={`max-w-[85%] rounded-2xl px-4 py-3 ${
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
                          <details className="mb-2.5 rounded-xl border border-line bg-white px-3 py-2 text-xs text-faint">
                            <summary className="cursor-pointer font-medium text-body">查看深度思考过程</summary>
                            <p className="mt-1.5 whitespace-pre-wrap leading-relaxed">{msg.reasoning}</p>
                          </details>
                        )}
                        {isUser ? (
                          <p className="whitespace-pre-wrap">{msg.content}</p>
                        ) : (
                          <MarkdownContent content={msg.content} />
                        )}
                        {!isUser && (
                          <div className="mt-2.5 flex flex-wrap items-center justify-between gap-2 border-t border-line/70 pt-2 text-[11px] text-faint">
                            <span className="tnum">
                              {msg.modelName || 'AI 助教'} {msg.durationMs ? `· ${msg.durationMs}ms` : ''}
                            </span>
                            <div className="flex items-center gap-1">
                              <CopyButton text={msg.content} label="复制" />
                              {isLastAssistant && !sending && (
                                <button
                                  type="button"
                                  onClick={() => void sendMessage(prevUser, { regenerate: true })}
                                  className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-faint hover:bg-soft hover:text-ink"
                                  title="使用当前模型与上下文重新生成回复"
                                >
                                  <RotateCcw className="h-3 w-3" />
                                  重新生成
                                </button>
                              )}
                              {prevUser && (
                                <button
                                  type="button"
                                  onClick={() => void handleSaveToBank(prevUser, msg.content)}
                                  className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-brand hover:bg-brand-soft"
                                >
                                  <BookmarkPlus className="h-3 w-3" />
                                  存入题库
                                </button>
                              )}
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}

                {/* 乐观展示刚刚发送的消息 + 实时 Agent 进度卡 */}
                {pendingUserMsg && (
                  <div className="flex justify-end animate-fade-in">
                    <div className="max-w-[85%] rounded-2xl bg-brand px-4 py-3 text-[13px] leading-relaxed text-white shadow-xs">
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

          {/* 多行自适应输入区 */}
          <form
            onSubmit={(e) => { e.preventDefault(); void sendMessage(); }}
            className="border-t border-line bg-white p-3"
          >
            <div className="flex items-end gap-2 rounded-xl border border-line bg-canvas/50 p-2 focus-within:border-brand focus-within:bg-white focus-within:ring-4 focus-within:ring-brand/15">
              <textarea
                ref={textareaRef}
                rows={1}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={enableTools ? '输入题目、学习需求或下达学习通指令（Enter 发送，Shift+Enter 换行）...' : '输入题目、知识点或复习需求（Enter 发送，Shift+Enter 换行）...'}
                disabled={sending}
                className="max-h-40 min-h-[38px] flex-1 resize-none bg-transparent px-2 py-1.5 text-sm text-ink placeholder:text-faint focus:outline-none"
              />
              {sending ? (
                <Button type="button" variant="destructive" size="sm" onClick={handleStopGeneration} className="shrink-0">
                  <Square className="h-3 w-3 fill-current" />
                  停止
                </Button>
              ) : (
                <Button type="submit" size="sm" disabled={!draft.trim()} className="shrink-0">
                  <Send className="h-3.5 w-3.5" />
                  发送
                </Button>
              )}
            </div>
            <div className="mt-1.5 flex items-center justify-between px-1 text-[10px] text-faint">
              <span className="inline-flex items-center gap-1">
                <CornerDownLeft className="h-3 w-3" />
                Enter 发送 · Shift + Enter 换行
              </span>
              {draft.length > 0 && <span className="tnum">{draft.length} 字</span>}
            </div>
          </form>
        </div>
      </div>

      <ConfirmDialog
        open={Boolean(sessionToDelete)}
        title={`删除对话「${sessionToDelete?.title || ''}」`}
        description="确定删除此助教会话及其全部聊天记录吗？"
        confirmText="确认删除"
        destructive
        onCancel={() => setSessionToDelete(null)}
        onConfirm={() => void handleConfirmDeleteSession()}
      />

      <ConfirmDialog
        open={confirmClear}
        title="清空当前对话记录"
        description="确定清空当前会话中的所有聊天消息吗？会话标题将保留。"
        confirmText="确认清空"
        destructive
        onCancel={() => setConfirmClear(false)}
        onConfirm={() => void handleConfirmClearSession()}
      />
    </div>
  );
}
