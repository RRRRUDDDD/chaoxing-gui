import { useState, useEffect, useCallback, useMemo } from 'react';
import Button from '../ui/Button';
import Input from '../ui/Input';
import Label from '../ui/Label';
import Select from '../ui/Select';
import { aiApi } from '../../api/aiApi';
import {
  ConfirmDialog, CopyButton, ToggleSwitch, QUESTION_TYPE_OPTIONS,
} from './aiShared';
import {
  RefreshCw, Trash2, Sparkles, Database,
  AlertTriangle, BookOpen, BrainCircuit, Search, ImagePlus,
  ChevronDown, ChevronUp, Loader2, ArrowRight, KeyRound, X, Eye,
  MessageSquare, RotateCcw,
} from 'lucide-react';

const SOURCE_LABELS = {
  local: { text: '本地命中', cls: 'bg-success/10 text-success-ink border-success/20' },
  local_same: { text: '同题复用', cls: 'bg-success/10 text-success-ink border-success/20' },
  ai: { text: 'AI 答题', cls: 'bg-brand-soft text-brand border-brand/20' },
  ai_vision: { text: '视觉直答', cls: 'bg-brand-soft text-brand border-brand/20' },
  error: { text: '失败', cls: 'bg-danger/10 text-danger border-danger/20' },
};

const SAMPLE_QUESTION = {
  title: '在操作系统中，采用分页存储管理的主要目的是什么？',
  options: 'A. 提高外存利用率\nB. 解决外部碎片问题，提高内存利用率\nC. 方便用户按逻辑模块编程\nD. 加快 CPU 指令译码速度',
  type: 'single',
};

function prettyJson(raw) {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return String(raw || '');
  }
}

function safeParseJson(raw) {
  try {
    return JSON.parse(raw || '{}');
  } catch {
    return {};
  }
}

export default function DashboardTab({
  config,
  stats,
  configuredPlatformCount = 0,
  onRefreshAll,
  onConfigSaved,
  onNavigate,
  onAskTutor,
  onToast,
}) {
  const [logs, setLogs] = useState([]);
  const [loadingLogs, setLoadingLogs] = useState(false);
  const [logFilter, setLogFilter] = useState('all');
  const [logSearch, setLogSearch] = useState('');
  const [selectedLogId, setSelectedLogId] = useState(null);
  const [confirmClearLogs, setConfirmClearLogs] = useState(false);

  const [showUnconfiguredModels, setShowUnconfiguredModels] = useState(false);

  // Test query state
  const [qTitle, setQTitle] = useState('');
  const [qOptions, setQOptions] = useState('');
  const [qType, setQType] = useState('single');
  const [qImages, setQImages] = useState([]);
  const [allowLocal, setAllowLocal] = useState(true);
  const [querying, setQuerying] = useState(false);
  const [queryResult, setQueryResult] = useState(null);
  const [errorMsg, setErrorMsg] = useState('');

  const loadLogs = useCallback(async () => {
    setLoadingLogs(true);
    try {
      const data = await aiApi.listLogs(60);
      setLogs(data.items || []);
    } catch (err) {
      setErrorMsg(err.message);
    } finally {
      setLoadingLogs(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    aiApi.listLogs(60)
      .then((data) => { if (!cancelled) setLogs(data.items || []); })
      .catch((err) => { if (!cancelled) setErrorMsg(err.message); });
    return () => { cancelled = true; };
  }, []);

  const availableModels = useMemo(() => {
    if (!config?.platforms) return [];
    const list = [];
    for (const p of config.platforms) {
      if (p.enabled === false) continue;
      for (const m of p.models || []) {
        if (m.enabled === false) continue;
        list.push({
          ...m,
          platformName: p.displayName || p.name,
          platformId: p.id,
          hasKey: Boolean(p.apiKey),
        });
      }
    }
    return list;
  }, [config]);

  const configuredModels = useMemo(() => availableModels.filter((m) => m.hasKey), [availableModels]);
  const unconfiguredModels = useMemo(() => availableModels.filter((m) => !m.hasKey), [availableModels]);

  const selectedText = config?.selectedTextModels || [];
  const selectedSummary = config?.selectedSummaryModels?.[0] || '';
  const selectedVision = config?.selectedVisionModel || '';

  const toggleTextModel = async (model) => {
    const isRemoving = selectedText.includes(model.id);
    if (!model.hasKey && !isRemoving) {
      onToast?.(`请先为「${model.platformName}」配置 API Key`, 'info');
      onNavigate?.('settings', { section: 'platforms', platformId: model.platformId, focusKey: true });
      return;
    }
    if (!isRemoving && selectedText.length >= 5) {
      onToast?.('最多同时选择 5 个基础答题模型并发投票', 'info');
      return;
    }
    const next = isRemoving
      ? selectedText.filter((id) => id !== model.id)
      : [...selectedText, model.id].slice(0, 5);
    try {
      const res = await aiApi.saveConfig({ selectedTextModels: next });
      onConfigSaved?.(res);
    } catch (err) {
      setErrorMsg(err.message);
    }
  };

  const updateSingleSelection = async (field, value) => {
    try {
      const payload = field === 'selectedSummaryModels'
        ? { selectedSummaryModels: value ? [value] : [] }
        : { [field]: value || null };
      const res = await aiApi.saveConfig(payload);
      onConfigSaved?.(res);
      onToast?.('模型调度配置已更新', 'success');
    } catch (err) {
      setErrorMsg(err.message);
    }
  };

  const appendImageFiles = (files) => {
    Array.from(files || []).slice(0, 4).forEach((file) => {
      if (!file.type?.startsWith('image/')) return;
      const reader = new FileReader();
      reader.onload = () => {
        if (typeof reader.result === 'string') {
          setQImages((prev) => [...prev, reader.result].slice(0, 4));
        }
      };
      reader.readAsDataURL(file);
    });
  };

  const handleImageUpload = (e) => {
    appendImageFiles(e.target.files);
    e.target.value = '';
  };

  const handlePaste = (e) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const files = [];
    for (const item of items) {
      if (item.type?.startsWith('image/')) {
        const f = item.getAsFile();
        if (f) files.push(f);
      }
    }
    if (files.length > 0) {
      appendImageFiles(files);
      onToast?.(`已从剪贴板粘贴 ${files.length} 张题目图片`, 'info');
    }
  };

  const executeQuery = async ({ forceAi = false } = {}) => {
    if (!qTitle.trim() || querying) return;
    setQuerying(true);
    setErrorMsg('');
    setQueryResult(null);
    try {
      const res = await aiApi.runQuery({
        title: qTitle.trim(),
        options: qOptions.trim(),
        type: qType,
        images: qImages,
        allowLocal: forceAi ? false : allowLocal,
        allowAi: true,
      });
      setQueryResult(res);
      await Promise.all([loadLogs(), onRefreshAll?.(true)]);
    } catch (err) {
      setErrorMsg(err.message);
    } finally {
      setQuerying(false);
    }
  };

  const handleRunTestQuery = async (e) => {
    e.preventDefault();
    await executeQuery({ forceAi: false });
  };

  const handleRefillFromLog = (log) => {
    const reqObj = safeParseJson(log.requestBody);
    const title = reqObj.title || reqObj.question || '';
    if (!title) {
      onToast?.('该日志中未解析出可重测的题干', 'info');
      return;
    }
    setQTitle(String(title));
    setQOptions(String(reqObj.options || ''));
    if (reqObj.type && QUESTION_TYPE_OPTIONS.some((t) => t.value === reqObj.type)) {
      setQType(reqObj.type);
    }
    setQueryResult(null);
    document.getElementById('ai-test-query')?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
    onToast?.('已将日志题目填入上方查题框', 'info');
  };

  const handleClearLogs = async () => {
    try {
      await aiApi.clearLogs();
      setLogs([]);
      setSelectedLogId(null);
      setConfirmClearLogs(false);
      onToast?.('查题请求日志已清空', 'success');
    } catch (err) {
      setErrorMsg(err.message);
    }
  };

  const filteredLogs = useMemo(() => {
    const q = logSearch.trim().toLowerCase();
    return logs.filter((log) => {
      if (logFilter === 'ok' && log.status >= 400) return false;
      if (logFilter === 'error' && log.status < 400) return false;
      if (logFilter === 'local' && !log.localSearch) return false;
      if (logFilter === 'ai' && (log.localSearch || log.status >= 400)) return false;
      if (q) {
        const hay = `${log.requestBody || ''} ${log.responseBody || ''} ${log.modelName || ''}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [logs, logFilter, logSearch]);

  const maxDaily = Math.max(1, ...(stats?.dailyCounts || []).map((d) => d.count));

  const kpiCards = [
    {
      label: '本地题库',
      value: stats?.totalQuestions ?? 0,
      sub: `AI 沉淀 ${stats?.aiQuestions ?? 0} 题`,
      icon: Database,
      iconCls: 'bg-brand-soft text-brand',
      onClick: () => onNavigate?.('questions', { folderId: -1 }),
    },
    {
      label: '今日查题',
      value: stats?.todayRequests ?? 0,
      sub: '点击查看下方日志',
      icon: Sparkles,
      iconCls: 'bg-indigo-50 text-indigo-600',
      onClick: () => document.getElementById('ai-logs-section')?.scrollIntoView?.({ behavior: 'smooth' }),
    },
    {
      label: '待修正错题',
      value: stats?.pendingCorrections ?? 0,
      sub: stats?.pendingCorrections > 0 ? '点击前往原地速改' : '暂无待复核错题',
      icon: AlertTriangle,
      iconCls: stats?.pendingCorrections > 0 ? 'bg-warning/15 text-warning-ink' : 'bg-soft text-faint',
      highlight: (stats?.pendingCorrections || 0) > 0,
      onClick: () => onNavigate?.('questions', { folderId: -2 }),
    },
    {
      label: '知识节点',
      value: stats?.knowledgeNodeCount ?? 0,
      sub: `${stats?.subjectCount ?? 0} 个学科图谱`,
      icon: BrainCircuit,
      iconCls: 'bg-success/10 text-success-ink',
      onClick: () => onNavigate?.('study'),
    },
    {
      label: '累计刷题',
      value: stats?.practiceTotal ?? 0,
      sub: '已联动遗忘曲线',
      icon: BookOpen,
      iconCls: 'bg-soft text-body',
      onClick: () => onNavigate?.('practice'),
    },
  ];

  const renderModelChip = (m) => {
    const active = selectedText.includes(m.id);
    return (
      <button
        key={m.id}
        type="button"
        onClick={() => void toggleTextModel(m)}
        title={m.hasKey ? '点击切换是否参与并发答题' : `点击前往配置 ${m.platformName} 的 API Key`}
        className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium transition ${
          active
            ? 'border-brand bg-brand-soft text-brand shadow-xs'
            : 'border-line bg-white text-body hover:border-faint/60 hover:bg-canvas'
        }`}
      >
        <span className="max-w-[160px] truncate">{m.displayName}</span>
        <span className="text-[10px] text-faint">{m.platformName}</span>
        {m.hasVision && (
          <span className="inline-flex items-center rounded bg-indigo-50 px-1 py-0.5 text-[10px] text-indigo-700" title="支持视觉图片题">
            <Eye className="h-2.5 w-2.5" />
          </span>
        )}
        {!m.hasKey && <span className="rounded bg-warning/15 px-1 text-[10px] text-warning-ink">去配Key</span>}
      </button>
    );
  };

  const isLocalResult = queryResult?.source === 'local' || queryResult?.source === 'local_same';

  return (
    <div className="space-y-5">
      {errorMsg && (
        <div role="alert" className="flex items-center justify-between rounded-xl border border-danger/25 bg-danger/5 px-4 py-2.5 text-xs text-danger">
          <span>{errorMsg}</span>
          <button type="button" onClick={() => setErrorMsg('')} className="ml-3 font-medium underline">关闭</button>
        </div>
      )}

      {/* 首次引导条：未配置任何 API Key 时展示 */}
      {config && configuredPlatformCount === 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-warning/30 bg-warning/5 px-5 py-3.5">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-warning/15 text-warning-ink">
              <KeyRound className="h-4 w-4" />
            </div>
            <div>
              <p className="text-sm font-semibold text-ink">尚未配置 AI 模型 API Key</p>
              <p className="text-xs text-body">本地题库可直接查询使用；在设置页填写任意平台 Key 并勾选角色即可一站式解锁 AI 答题与助教。</p>
            </div>
          </div>
          <Button size="sm" onClick={() => onNavigate?.('settings', { section: 'platforms', focusKey: true })}>
            前往配置 API Key
            <ArrowRight className="h-3.5 w-3.5" />
          </Button>
        </div>
      )}

      {/* 顶部可点击指标卡 */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {kpiCards.map((card) => (
          <button
            key={card.label}
            type="button"
            onClick={card.onClick}
            className={`group rounded-2xl border bg-white p-4 text-left shadow-card transition hover:-translate-y-0.5 hover:shadow-lift ${
              card.highlight ? 'border-warning/40' : 'border-line hover:border-brand/30'
            }`}
          >
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-faint">{card.label}</span>
              <span className={`flex h-7 w-7 items-center justify-center rounded-lg ${card.iconCls}`}>
                <card.icon className="h-3.5 w-3.5" aria-hidden="true" />
              </span>
            </div>
            <p className="mt-2 text-2xl font-bold tracking-tight text-ink tnum">{card.value}</p>
            <p className="mt-1 flex items-center justify-between text-[11px] text-faint">
              <span className="truncate">{card.sub}</span>
              <ArrowRight className="h-3 w-3 shrink-0 opacity-0 transition group-hover:translate-x-0.5 group-hover:opacity-100" />
            </p>
          </button>
        ))}
      </div>

      {/* 主操作区：左侧查题测试 + 右侧模型调度 */}
      <div className="grid gap-5 xl:grid-cols-12">
        {/* 在线查题测试 */}
        <section id="ai-test-query" className="flex flex-col rounded-2xl border border-line bg-white p-5 shadow-card xl:col-span-7">
          <div className="mb-4 flex flex-wrap items-start justify-between gap-2">
            <div>
              <h2 className="text-sm font-semibold text-ink">在线查题测试（完整管线）</h2>
              <p className="mt-0.5 text-xs text-faint">
                本地题库 → AI 智能同题判重 → 联网搜题 → 视觉 / 多模型分层答题
              </p>
            </div>
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() => {
                  setQTitle(SAMPLE_QUESTION.title);
                  setQOptions(SAMPLE_QUESTION.options);
                  setQType(SAMPLE_QUESTION.type);
                }}
                className="text-xs font-medium text-brand hover:underline"
              >
                填入示例题
              </button>
              <ToggleSwitch
                id="dash-allow-local"
                checked={allowLocal}
                onChange={setAllowLocal}
                label="优先查本地"
              />
            </div>
          </div>

          <form onSubmit={handleRunTestQuery} className="space-y-3" onPaste={handlePaste}>
            <div className="grid gap-3 sm:grid-cols-12">
              <div className="space-y-1 sm:col-span-9">
                <Label htmlFor="test-q-title">题目题干（Ctrl+V 粘贴截图 · Ctrl+Enter 快捷查题）</Label>
                <textarea
                  id="test-q-title"
                  rows={2}
                  placeholder="输入或粘贴待测试的题目题干..."
                  value={qTitle}
                  onChange={(e) => setQTitle(e.target.value)}
                  onKeyDown={(e) => {
                    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                      e.preventDefault();
                      void executeQuery({ forceAi: false });
                    }
                  }}
                  required
                  className="flex w-full rounded-lg border border-line bg-white px-3 py-2 text-sm text-ink placeholder:text-faint transition hover:border-faint/50 focus:border-brand focus:outline-none focus:ring-4 focus:ring-brand/15"
                />
              </div>
              <div className="space-y-1 sm:col-span-3">
                <Label htmlFor="test-q-type">题型</Label>
                <Select id="test-q-type" value={qType} onChange={(e) => setQType(e.target.value)}>
                  {QUESTION_TYPE_OPTIONS.map((t) => (
                    <option key={t.value} value={t.value}>{t.label}</option>
                  ))}
                </Select>
              </div>
            </div>

            {(qType === 'single' || qType === 'multiple' || qOptions.trim()) && (
              <div className="space-y-1">
                <Label htmlFor="test-q-options">选项（每行一个，选填）</Label>
                <textarea
                  id="test-q-options"
                  rows={3}
                  placeholder={'A. 选项一\nB. 选项二\nC. 选项三\nD. 选项四'}
                  value={qOptions}
                  onChange={(e) => setQOptions(e.target.value)}
                  className="flex w-full rounded-lg border border-line bg-white px-3 py-2 text-xs text-ink placeholder:text-faint transition hover:border-faint/50 focus:border-brand focus:outline-none focus:ring-4 focus:ring-brand/15"
                />
              </div>
            )}

            {qImages.length > 0 && (
              <div className="flex flex-wrap gap-2 rounded-xl border border-line bg-canvas p-2.5">
                {qImages.map((src, idx) => (
                  <div key={idx} className="group relative h-16 w-16 overflow-hidden rounded-lg border border-line bg-white">
                    <img src={src} alt={`题目附图 ${idx + 1}`} className="h-full w-full object-cover" />
                    <button
                      type="button"
                      onClick={() => setQImages((prev) => prev.filter((_, i) => i !== idx))}
                      aria-label={`移除图片 ${idx + 1}`}
                      className="absolute right-0.5 top-0.5 flex h-5 w-5 items-center justify-center rounded-full bg-ink/70 text-white opacity-90 hover:bg-danger"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </div>
                ))}
              </div>
            )}

            <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
              <div className="flex items-center gap-2">
                <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-line bg-canvas px-3 py-1.5 text-xs font-medium text-body transition hover:bg-soft hover:text-ink">
                  <ImagePlus className="h-3.5 w-3.5" />
                  <span>上传图片（最多4张）</span>
                  <input type="file" accept="image/*" multiple onChange={handleImageUpload} className="hidden" />
                </label>
                {qTitle && (
                  <button
                    type="button"
                    onClick={() => { setQTitle(''); setQOptions(''); setQImages([]); setQueryResult(null); }}
                    className="text-xs text-faint hover:text-ink"
                  >
                    清空输入
                  </button>
                )}
              </div>
              <Button type="submit" size="sm" disabled={querying || !qTitle.trim()}>
                {querying ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />}
                {querying ? '查题中...' : '运行查题'}
              </Button>
            </div>
          </form>

          {queryResult && (
            <div className="mt-4 space-y-2.5 rounded-xl border border-line bg-canvas/80 p-4 animate-fade-in">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${(SOURCE_LABELS[queryResult.source] || SOURCE_LABELS.error).cls}`}>
                    {(SOURCE_LABELS[queryResult.source] || SOURCE_LABELS.error).text}
                  </span>
                  {queryResult.modelName && <span className="text-xs text-faint">{queryResult.modelName}</span>}
                </div>
                <span className="text-xs text-faint tnum">{queryResult.durationMs} ms</span>
              </div>

              {queryResult.ok ? (
                <div className="rounded-xl border border-line bg-white p-3.5 space-y-2.5">
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] font-medium text-faint">最终答案</span>
                    <CopyButton text={queryResult.answer} label="复制答案" />
                  </div>
                  <p className="whitespace-pre-wrap text-sm font-semibold leading-relaxed text-ink">
                    {queryResult.answer}
                  </p>
                  {/* 查题结果原地闭环操作条 */}
                  <div className="flex flex-wrap items-center gap-1.5 border-t border-line/70 pt-2.5">
                    {isLocalResult && (
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        disabled={querying}
                        onClick={() => void executeQuery({ forceAi: true })}
                        className="h-7 text-[11px]"
                      >
                        <RotateCcw className="h-3 w-3" />
                        跳过本地，用 AI 重答
                      </Button>
                    )}
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      onClick={() =>
                        onAskTutor?.({
                          contextBadge: '查题结果追问',
                          contextTitle: qTitle.trim().slice(0, 40),
                          autoSend: true,
                          prompt: `请帮我核验并详细讲解这道题：\n题目：${qTitle.trim()}${qOptions.trim() ? `\n选项：\n${qOptions.trim()}` : ''}\n当前查到的答案：${queryResult.answer}`,
                        })
                      }
                      className="h-7 text-[11px]"
                    >
                      <MessageSquare className="h-3 w-3 text-brand" />
                      追问 AI 助教
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      onClick={() => onNavigate?.('questions', { folderId: -1, keyword: qTitle.trim().slice(0, 24) })}
                      className="h-7 text-[11px]"
                    >
                      <Database className="h-3 w-3" />
                      去题库查看
                    </Button>
                  </div>
                </div>
              ) : (
                <p className="rounded-lg border border-danger/20 bg-danger/5 p-3 text-xs text-danger">{queryResult.error}</p>
              )}

              {queryResult.searchTraces?.length > 0 && (
                <details className="rounded-lg border border-line bg-white p-2.5 text-xs">
                  <summary className="cursor-pointer font-medium text-body">
                    联网搜题轨迹（{queryResult.searchTraces[0].provider} · {queryResult.searchTraces[0].results?.length || 0} 条）
                  </summary>
                  <div className="mt-2 space-y-1.5 text-faint">
                    {(queryResult.searchTraces[0].results || []).map((r, idx) => (
                      <div key={idx} className="border-t border-line/60 pt-1.5">
                        <p className="font-medium text-ink">{r.title}</p>
                        <p className="line-clamp-2">{r.snippet}</p>
                      </div>
                    ))}
                  </div>
                </details>
              )}
              {queryResult.reasoning && (
                <details className="rounded-lg border border-line bg-white p-2.5 text-xs">
                  <summary className="cursor-pointer font-medium text-body">查看推理/思考过程</summary>
                  <pre className="mt-2 whitespace-pre-wrap font-sans leading-relaxed text-faint">{queryResult.reasoning}</pre>
                </details>
              )}
            </div>
          )}
        </section>

        {/* 右侧：模型调度 */}
        <div className="space-y-4 xl:col-span-5">
          {/* 分层答题模型调度 */}
          <section className="space-y-4 rounded-2xl border border-line bg-white p-5 shadow-card">
            <div className="flex items-start justify-between gap-2">
              <div>
                <h2 className="text-sm font-semibold text-ink">分层答题模型调度</h2>
                <p className="mt-0.5 text-xs text-faint">
                  并发投票 · 高阶复核 · 图片题视觉直答（也可在设置页配 Key 时直接分配）
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                className="shrink-0 whitespace-nowrap"
                onClick={() => onNavigate?.('settings', { section: 'platforms' })}
              >
                配置 API Key
              </Button>
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label>基础答题模型（可多选）</Label>
                <span className="text-[11px] font-medium text-faint tnum">已选 {selectedText.length} / 5</span>
              </div>

              {configuredModels.length > 0 ? (
                <div className="flex flex-wrap gap-1.5">
                  {configuredModels.map(renderModelChip)}
                </div>
              ) : (
                <p className="rounded-lg border border-dashed border-line bg-canvas px-3 py-2 text-xs text-faint">
                  暂无已配置 API Key 的模型，请先前往「模型与搜题设置」填写任意平台 Key。
                </p>
              )}

              {unconfiguredModels.length > 0 && (
                <div>
                  <button
                    type="button"
                    onClick={() => setShowUnconfiguredModels((v) => !v)}
                    className="inline-flex items-center gap-1 text-[11px] text-faint hover:text-ink"
                  >
                    {showUnconfiguredModels ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                    {showUnconfiguredModels ? '收起未配置 Key 的模型' : `显示未配置 Key 的平台模型 (${unconfiguredModels.length})`}
                  </button>
                  {showUnconfiguredModels && (
                    <div className="mt-2 flex flex-wrap gap-1.5 rounded-xl border border-line bg-canvas/60 p-2.5">
                      {unconfiguredModels.map(renderModelChip)}
                    </div>
                  )}
                </div>
              )}
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1">
                <Label htmlFor="dash-summary-model">汇总复核模型（可选）</Label>
                <Select
                  id="dash-summary-model"
                  value={selectedSummary}
                  onChange={(e) => void updateSingleSelection('selectedSummaryModels', e.target.value)}
                  className="h-9 text-xs"
                >
                  <option value="">自动多数投票（不启用）</option>
                  {availableModels.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.hasKey ? '' : '[缺Key] '}{m.platformName} / {m.displayName}
                    </option>
                  ))}
                </Select>
              </div>
              <div className="space-y-1">
                <Label htmlFor="dash-vision-model">图片视觉直答模型</Label>
                <Select
                  id="dash-vision-model"
                  value={selectedVision}
                  onChange={(e) => void updateSingleSelection('selectedVisionModel', e.target.value)}
                  className="h-9 text-xs"
                >
                  <option value="">自动选用已配置视觉模型</option>
                  {availableModels.filter((m) => m.hasVision).map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.hasKey ? '' : '[缺Key] '}{m.platformName} / {m.displayName}
                    </option>
                  ))}
                </Select>
              </div>
            </div>

            {/* 近 7 天查题趋势 */}
            <div className="rounded-xl border border-line bg-canvas/70 p-3">
              <div className="mb-2 flex items-center justify-between text-xs">
                <span className="font-medium text-body">近 7 天查题趋势</span>
                <span className="text-faint tnum">今日 {stats?.todayRequests ?? 0} 次</span>
              </div>
              <div className="grid h-16 grid-cols-7 items-end gap-2 pt-2">
                {(stats?.dailyCounts || []).map((item) => {
                  const pct = item.count > 0 ? Math.max(14, Math.round((item.count / maxDaily) * 100)) : 6;
                  return (
                    <div key={item.day} className="group flex flex-col items-center gap-1" title={`${item.day}: ${item.count} 次`}>
                      <span className="text-[10px] text-faint tnum opacity-80 group-hover:font-semibold group-hover:text-brand">
                        {item.count > 0 ? item.count : ''}
                      </span>
                      <div className="flex h-9 w-full items-end justify-center">
                        <div
                          className={`w-full max-w-[26px] rounded-t transition-all ${
                            item.count > 0 ? 'bg-brand/80 group-hover:bg-brand' : 'bg-line/80'
                          }`}
                          style={{ height: `${pct}%` }}
                        />
                      </div>
                      <span className="text-[10px] text-faint tnum">{item.day.slice(5)}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          </section>
        </div>
      </div>

      {/* 查题请求日志 */}
      <section id="ai-logs-section" className="space-y-3 rounded-2xl border border-line bg-white p-5 shadow-card">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold text-ink">查题请求日志</h2>
            <span className="rounded-full bg-soft px-2 py-0.5 text-[11px] text-faint tnum">
              {filteredLogs.length} / {logs.length}
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <div className="inline-flex rounded-lg border border-line bg-canvas p-0.5 text-[11px]">
              {[
                { id: 'all', label: '全部' },
                { id: 'ok', label: '成功' },
                { id: 'error', label: '失败' },
                { id: 'local', label: '本地' },
                { id: 'ai', label: 'AI' },
              ].map((f) => (
                <button
                  key={f.id}
                  type="button"
                  onClick={() => setLogFilter(f.id)}
                  className={`rounded-md px-2 py-1 font-medium transition ${
                    logFilter === f.id ? 'bg-white text-ink shadow-xs' : 'text-faint hover:text-ink'
                  }`}
                >
                  {f.label}
                </button>
              ))}
            </div>
            <div className="relative w-44">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-faint" />
              <Input
                value={logSearch}
                onChange={(e) => setLogSearch(e.target.value)}
                placeholder="筛选日志..."
                className="h-8 pl-8 text-xs"
              />
            </div>
            <Button variant="outline" size="sm" onClick={loadLogs} disabled={loadingLogs}>
              <RefreshCw className={`h-3.5 w-3.5 ${loadingLogs ? 'animate-spin' : ''}`} />
              刷新
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setConfirmClearLogs(true)} disabled={!logs.length}>
              <Trash2 className="h-3.5 w-3.5 text-danger" />
              清空
            </Button>
          </div>
        </div>

        {filteredLogs.length === 0 ? (
          <p className="rounded-xl border border-dashed border-line py-8 text-center text-xs text-faint">
            {logs.length === 0 ? '暂无查题记录，可在上方运行一次查题测试' : '没有符合当前筛选条件的日志'}
          </p>
        ) : (
          <div className="divide-y divide-line/80 overflow-hidden rounded-xl border border-line">
            {filteredLogs.slice(0, 30).map((log) => {
              const expanded = selectedLogId === log.logId;
              const reqObj = safeParseJson(log.requestBody);
              const resObj = safeParseJson(log.responseBody);
              const titlePreview = reqObj.title || reqObj.question || log.path;
              const answerPreview = resObj.answer || resObj.error || '';

              return (
                <div key={log.logId} className="bg-white">
                  <div className="flex items-center gap-2 px-4 py-2.5 text-xs transition hover:bg-canvas">
                    <button
                      type="button"
                      onClick={() => setSelectedLogId(expanded ? null : log.logId)}
                      className="flex min-w-0 flex-1 items-center justify-between gap-3 text-left"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className={`shrink-0 rounded px-1.5 py-0.5 font-mono text-[10px] font-medium ${log.status < 400 ? 'bg-success/10 text-success-ink' : 'bg-danger/10 text-danger'}`}>
                            {log.status}
                          </span>
                          <span className="truncate font-medium text-ink">{titlePreview}</span>
                        </div>
                        {answerPreview && (
                          <p className="mt-0.5 truncate pl-9 text-faint">答：{answerPreview}</p>
                        )}
                      </div>
                      <div className="flex shrink-0 items-center gap-2 text-[11px] text-faint tnum">
                        {log.localSearch && (
                          <span className="rounded bg-soft px-1.5 py-0.5 text-body">{log.localSearch}</span>
                        )}
                        {log.modelName && <span className="hidden sm:inline">{log.modelName}</span>}
                        <span>{log.responseTime}ms</span>
                        {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
                      </div>
                    </button>

                    {(reqObj.title || reqObj.question) && (
                      <div className="flex shrink-0 items-center gap-1">
                        <button
                          type="button"
                          onClick={() => handleRefillFromLog(log)}
                          title="填入上方查题框重新测试"
                          className="rounded-md px-2 py-1 text-[11px] font-medium text-faint transition hover:bg-soft hover:text-brand"
                        >
                          重测
                        </button>
                        <button
                          type="button"
                          onClick={() =>
                            onAskTutor?.({
                              contextBadge: '查题日志分析',
                              contextTitle: String(titlePreview).slice(0, 40),
                              autoSend: true,
                              prompt: `请帮我分析这条查题记录：\n题目：${titlePreview}${reqObj.options ? `\n选项：\n${reqObj.options}` : ''}\n返回结果：${answerPreview || '(无)'}`,
                            })
                          }
                          title="用快捷 AI 助教分析此题"
                          className="rounded-md p-1 text-faint transition hover:bg-soft hover:text-brand"
                        >
                          <MessageSquare className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    )}
                  </div>
                  {expanded && (
                    <div className="space-y-3 border-t border-line bg-canvas/70 px-4 py-3 text-xs">
                      <div className="grid gap-3 md:grid-cols-2">
                        <div>
                          <div className="mb-1 flex items-center justify-between">
                            <p className="font-medium text-body">请求内容</p>
                            <CopyButton text={prettyJson(log.requestBody)} />
                          </div>
                          <pre className="max-h-40 overflow-auto rounded-lg border border-line bg-white p-2.5 font-mono text-[11px] text-body">
                            {prettyJson(log.requestBody)}
                          </pre>
                        </div>
                        <div>
                          <div className="mb-1 flex items-center justify-between">
                            <p className="font-medium text-body">响应结果</p>
                            <CopyButton text={prettyJson(log.responseBody)} />
                          </div>
                          <pre className="max-h-40 overflow-auto rounded-lg border border-line bg-white p-2.5 font-mono text-[11px] text-body">
                            {prettyJson(log.responseBody)}
                          </pre>
                        </div>
                      </div>
                      {log.reasoning && (
                        <div>
                          <p className="mb-1 font-medium text-body">推理过程</p>
                          <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-white p-2.5 font-sans text-[11px] text-faint">
                            {log.reasoning}
                          </pre>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>

      <ConfirmDialog
        open={confirmClearLogs}
        title="清空查题请求日志"
        description={`确定清空全部 ${logs.length} 条查题请求日志吗？此操作不会影响本地题库数据。`}
        confirmText="清空日志"
        destructive
        onCancel={() => setConfirmClearLogs(false)}
        onConfirm={() => void handleClearLogs()}
      />
    </div>
  );
}
