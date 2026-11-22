import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import Button from '../ui/Button';
import Input from '../ui/Input';
import Label from '../ui/Label';
import Select from '../ui/Select';
import { aiApi } from '../../api/aiApi';
import { ToggleSwitch, ConfirmDialog } from './aiShared';
import {
  Plus, Trash2, Save, Eye, EyeOff, CheckCircle2, XCircle, Loader2,
  Globe, Cpu, Sliders, PlugZap, RotateCcw, Search, ChevronDown, ChevronUp,
  ExternalLink, Sparkles, Check,
} from 'lucide-react';

const SETTINGS_SECTIONS = [
  { id: 'platforms', label: 'AI 供应商与模型', icon: Cpu },
  { id: 'search', label: '联网搜题增强', icon: Globe },
  { id: 'engine', label: '双题库与协同引擎', icon: Sliders },
];

const PRIORITY_CARDS = [
  {
    id: 'local_ocs_ai',
    title: '本地 SQLite → 外部 OCS → 内置 AI',
    desc: '推荐：优先秒答本地题库，未命中再查外部 OCS，最后由 AI 兜底并自动沉淀。',
  },
  {
    id: 'local_ai_ocs',
    title: '本地 SQLite → 内置 AI → 外部 OCS',
    desc: '信任 AI 推理质量时使用，本地未命中优先走多模型答题。',
  },
  {
    id: 'local_ai_only',
    title: '仅本地 SQLite + 内置 AI',
    desc: '不请求任何外部第三方 OCS 题库，完全自给自足。',
  },
  {
    id: 'ocs_only',
    title: '仅外部 OCS（答案自动入库）',
    desc: '不消耗 AI Token，仅通过外部 OCS 查题并将结果沉淀到本地。',
  },
];

const DEFAULT_SEARCH_PROVIDERS = [
  { id: 'tavily', name: 'Tavily', keyField: 'tavilyApiKey', website: 'https://www.tavily.com/' },
  { id: 'brave', name: 'Brave Search', keyField: 'braveApiKey', website: 'https://brave.com/search/api/' },
  { id: 'exa', name: 'Exa', keyField: 'exaApiKey', website: 'https://exa.ai/' },
  { id: 'zhipu', name: '智谱联网搜索', keyField: 'zhipuApiKey', website: 'https://open.bigmodel.cn/' },
  { id: 'you', name: 'You.com', keyField: 'youApiKey', website: 'https://you.com/' },
  { id: 'deepseek', name: 'DeepSeek', keyField: 'deepseekApiKey', website: 'https://api-docs.deepseek.com/' },
];

export default function ModelSettingsTab({
  isActive = true,
  config,
  searchProviders = [],
  intent,
  onConfigSaved,
  onDirtyChange,
  onToast,
}) {
  const [section, setSection] = useState(() => intent?.section || 'platforms');
  const [draft, setDraft] = useState(config);
  const [activePlatformId, setActivePlatformId] = useState(
    intent?.platformId || config?.platforms?.[0]?.id || 'deepseek'
  );
  const [platformQuery, setPlatformQuery] = useState('');
  const [showKey, setShowKey] = useState(false);
  const [showSearchKey, setShowSearchKey] = useState(false);
  const [expandedModelId, setExpandedModelId] = useState(null);
  const [platformToDelete, setPlatformToDelete] = useState(null);

  const [saving, setSaving] = useState(false);
  const [testingModelId, setTestingModelId] = useState(null);
  const [testResults, setTestResults] = useState({});
  const [testingSearch, setTestingSearch] = useState(false);
  const [searchTestResult, setSearchTestResult] = useState(null);
  const [folders, setFolders] = useState([]);
  const [newModelId, setNewModelId] = useState('');
  const [newModelName, setNewModelName] = useState('');
  const [newModelVision, setNewModelVision] = useState(false);
  const [newModelThinking, setNewModelThinking] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');

  const apiKeyInputRef = useRef(null);
  const handledIntentRef = useRef(null);

  // Sync draft ONLY when upstream config changes (never wipe draft on activePlatformId change!)
  useEffect(() => {
    setDraft(config);
    setActivePlatformId((prev) => {
      if (config?.platforms?.length && !config.platforms.some((p) => p.id === prev)) {
        return config.platforms[0].id;
      }
      return prev;
    });
  }, [config]);

  // React to navigation intent (e.g., clicking a missing-key model or "刷课协同已启用")
  useEffect(() => {
    if (!intent?._ts || handledIntentRef.current === intent._ts) return;
    handledIntentRef.current = intent._ts;
    if (intent.section) {
      setSection(intent.section);
    }
    if (intent.platformId) {
      setSection('platforms');
      setActivePlatformId(intent.platformId);
      setShowKey(false);
    }
    if (intent.focusKey) {
      setTimeout(() => apiKeyInputRef.current?.focus(), 80);
    }
  }, [intent]);

  useEffect(() => {
    aiApi.listFolders().then((res) => setFolders(res.items || [])).catch(() => {});
  }, []);

  const isDirty = useMemo(() => {
    if (!config || !draft) return false;
    return JSON.stringify(config) !== JSON.stringify(draft);
  }, [config, draft]);

  useEffect(() => {
    onDirtyChange?.(isDirty);
    return () => onDirtyChange?.(false);
  }, [isDirty, onDirtyChange]);

  const handleSave = useCallback(async () => {
    if (!draft || saving) return;
    setSaving(true);
    setErrorMsg('');
    try {
      const res = await aiApi.saveConfig({ config: draft });
      onConfigSaved?.(res);
      onToast?.('AI 配置已保存（API Key 已自动接入日志脱敏保护）', 'success');
    } catch (err) {
      setErrorMsg(err.message);
    } finally {
      setSaving(false);
    }
  }, [draft, saving, onConfigSaved, onToast]);

  // Ctrl+S / Cmd+S shortcut when this tab is active
  useEffect(() => {
    if (!isActive) return undefined;
    const onKeyDown = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault();
        if (isDirty) void handleSave();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [isActive, isDirty, handleSave]);

  const activePlatform = useMemo(
    () => (draft?.platforms || []).find((p) => p.id === activePlatformId) || null,
    [draft, activePlatformId]
  );

  const filteredPlatforms = useMemo(() => {
    const q = platformQuery.trim().toLowerCase();
    const list = draft?.platforms || [];
    if (!q) return list;
    return list.filter(
      (p) => (p.displayName || '').toLowerCase().includes(q) || (p.id || '').toLowerCase().includes(q)
    );
  }, [draft?.platforms, platformQuery]);

  const allModels = useMemo(() => {
    const out = [];
    for (const p of draft?.platforms || []) {
      if (p.enabled === false) continue;
      for (const m of p.models || []) {
        if (m.enabled !== false) {
          out.push({ ...m, platformName: p.displayName, hasKey: Boolean(p.apiKey) });
        }
      }
    }
    return out;
  }, [draft?.platforms]);

  const modelLookup = useMemo(() => {
    const map = new Map();
    for (const p of draft?.platforms || []) {
      for (const m of p.models || []) {
        map.set(m.id, `${p.displayName} / ${m.displayName}`);
      }
    }
    return map;
  }, [draft?.platforms]);

  if (!draft) return null;

  const selectedTextModels = draft.selectedTextModels || [];
  const selectedSummaryModel = draft.selectedSummaryModels?.[0] || '';
  const selectedVisionModel = draft.selectedVisionModel || '';
  const selectedAgentModel = draft.selectedAgentModel || '';

  const toggleRoleText = (modelId) => {
    const exists = selectedTextModels.includes(modelId);
    if (!exists && selectedTextModels.length >= 5) {
      onToast?.('最多同时选择 5 个基础答题模型并发投票', 'info');
      return;
    }
    const next = exists
      ? selectedTextModels.filter((id) => id !== modelId)
      : [...selectedTextModels, modelId];
    setDraft((prev) => ({ ...prev, selectedTextModels: next }));
  };

  const toggleRoleSummary = (modelId) => {
    setDraft((prev) => ({
      ...prev,
      selectedSummaryModels: prev.selectedSummaryModels?.[0] === modelId ? [] : [modelId],
    }));
  };

  const toggleRoleVision = (modelId) => {
    setDraft((prev) => ({
      ...prev,
      selectedVisionModel: prev.selectedVisionModel === modelId ? null : modelId,
    }));
  };

  const toggleRoleAgent = (modelId) => {
    setDraft((prev) => ({
      ...prev,
      selectedAgentModel: prev.selectedAgentModel === modelId ? null : modelId,
    }));
  };

  const updatePlatform = (pid, patch) => {
    setDraft((prev) => ({
      ...prev,
      platforms: prev.platforms.map((p) => (p.id === pid ? { ...p, ...patch } : p)),
    }));
  };

  const handleAddPlatform = () => {
    const id = `custom_${Date.now()}`;
    const created = {
      id,
      name: '自定义平台',
      displayName: '自定义 OpenAI 兼容平台',
      baseUrl: 'https://api.example.com/v1',
      apiProtocol: 'openai_chat',
      apiKey: '',
      enabled: true,
      customHeaders: {},
      isPreset: false,
      models: [],
    };
    setDraft((prev) => ({ ...prev, platforms: [...prev.platforms, created] }));
    setActivePlatformId(id);
    setPlatformQuery('');
  };

  const handleConfirmDeletePlatform = () => {
    if (!platformToDelete) return;
    const pid = platformToDelete.id;
    setDraft((prev) => {
      const next = prev.platforms.filter((p) => p.id !== pid);
      if (activePlatformId === pid) setActivePlatformId(next[0]?.id || '');
      return { ...prev, platforms: next };
    });
    setPlatformToDelete(null);
  };

  const handleAddModel = (e) => {
    e.preventDefault();
    if (!activePlatform || !newModelId.trim()) return;
    const rawModelId = newModelId.trim();
    const uniqueId = `${activePlatform.id}/${rawModelId}`;
    if (activePlatform.models.some((m) => m.id === uniqueId)) {
      setErrorMsg('该模型 ID 已存在于当前平台');
      return;
    }
    const model = {
      id: uniqueId,
      modelId: rawModelId,
      displayName: newModelName.trim() || rawModelId,
      platformId: activePlatform.id,
      enabled: true,
      hasVision: newModelVision,
      enableThinking: newModelThinking,
      thinkingEffort: 'medium',
      temperature: 0.2,
      maxTokens: 4096,
    };
    updatePlatform(activePlatform.id, { models: [...activePlatform.models, model] });
    setNewModelId('');
    setNewModelName('');
    setNewModelVision(false);
    setNewModelThinking(false);
    setErrorMsg('');
  };

  const handleUpdateModel = (pid, mid, patch) => {
    setDraft((prev) => ({
      ...prev,
      platforms: prev.platforms.map((p) =>
        p.id === pid
          ? { ...p, models: p.models.map((m) => (m.id === mid ? { ...m, ...patch } : m)) }
          : p
      ),
    }));
  };

  const handleDeleteModel = (pid, mid) => {
    setDraft((prev) => ({
      ...prev,
      platforms: prev.platforms.map((p) =>
        p.id === pid ? { ...p, models: p.models.filter((m) => m.id !== mid) } : p
      ),
      selectedTextModels: (prev.selectedTextModels || []).filter((id) => id !== mid),
      selectedSummaryModels: (prev.selectedSummaryModels || []).filter((id) => id !== mid),
      selectedVisionModel: prev.selectedVisionModel === mid ? null : prev.selectedVisionModel,
      selectedAgentModel: prev.selectedAgentModel === mid ? null : prev.selectedAgentModel,
    }));
  };

  const handleTestModel = async (platform, model) => {
    setTestingModelId(model.id);
    try {
      const res = await aiApi.testModel({ platform, model });
      setTestResults((prev) => ({ ...prev, [model.id]: res }));
    } catch (err) {
      setTestResults((prev) => ({ ...prev, [model.id]: { ok: false, error: err.message } }));
    } finally {
      setTestingModelId(null);
    }
  };

  const handleTestSearch = async () => {
    setTestingSearch(true);
    setSearchTestResult(null);
    try {
      const res = await aiApi.testSearch({ search: draft.search, query: '光合作用的产物是什么' });
      setSearchTestResult(res);
    } catch (err) {
      setSearchTestResult({ status: 'error', error: err.message });
    } finally {
      setTestingSearch(false);
    }
  };

  const effectiveSearchProviders = searchProviders.length ? searchProviders : DEFAULT_SEARCH_PROVIDERS;
  const currentSearchProvider =
    effectiveSearchProviders.find((s) => s.id === draft.search?.provider) || effectiveSearchProviders[0];

  return (
    <div className="space-y-4">
      {/* 顶部保存条（吸顶） */}
      <div className="sticky top-0 z-10 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-line bg-white/95 px-5 py-3.5 shadow-card backdrop-blur">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-ink">AI 供应商、联网搜题与协同引擎设置</h2>
            {isDirty && (
              <span className="rounded-full bg-warning/15 px-2 py-0.5 text-[11px] font-medium text-warning-ink">
                有未保存修改
              </span>
            )}
          </div>
          <p className="mt-0.5 text-xs text-faint">配 Key、测连通、分配「答题 / 复核 / 视觉 / 助教」一站式完成（Ctrl+S 快捷保存）</p>
        </div>

        <div className="flex items-center gap-2">
          {isDirty && (
            <Button size="sm" variant="outline" onClick={() => { setDraft(config); setErrorMsg(''); }}>
              <RotateCcw className="h-3.5 w-3.5" />
              还原修改
            </Button>
          )}
          <Button size="sm" onClick={handleSave} disabled={saving}>
            {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
            {saving ? '保存中...' : '保存全部设置'}
          </Button>
        </div>
      </div>

      {errorMsg && (
        <div role="alert" className="flex items-center justify-between rounded-xl border border-danger/25 bg-danger/5 px-4 py-2.5 text-xs text-danger">
          <span>{errorMsg}</span>
          <button type="button" onClick={() => setErrorMsg('')} className="underline">关闭</button>
        </div>
      )}

      {/* 分组子导航 */}
      <div className="inline-flex flex-wrap gap-1 rounded-xl border border-line bg-white p-1 shadow-card">
        {SETTINGS_SECTIONS.map((s) => {
          const active = section === s.id;
          const Icon = s.icon;
          return (
            <button
              key={s.id}
              type="button"
              onClick={() => setSection(s.id)}
              className={`inline-flex items-center gap-1.5 rounded-lg px-3.5 py-2 text-xs font-medium transition ${
                active ? 'bg-brand text-white shadow-xs' : 'text-body hover:bg-soft hover:text-ink'
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              <span>{s.label}</span>
            </button>
          );
        })}
      </div>

      {/* 子页 1：AI 供应商与模型 */}
      {section === 'platforms' && (
        <div className="space-y-4">
          {/* 当前生效模型分配总览条 */}
          <div className="grid gap-2.5 rounded-2xl border border-line bg-white p-3.5 text-xs shadow-card sm:grid-cols-2 xl:grid-cols-4">
            <div className="rounded-xl bg-canvas/80 px-3 py-2">
              <p className="text-[11px] text-faint">基础答题模型 ({selectedTextModels.length}/5)</p>
              <p className="mt-0.5 truncate font-medium text-ink">
                {selectedTextModels.length > 0
                  ? selectedTextModels.map((id) => modelLookup.get(id) || id).join('、')
                  : '未选择（请在下方勾选「答题」）'}
              </p>
            </div>
            <div className="rounded-xl bg-canvas/80 px-3 py-2">
              <p className="text-[11px] text-faint">汇总复核模型</p>
              <p className="mt-0.5 truncate font-medium text-ink">
                {selectedSummaryModel ? (modelLookup.get(selectedSummaryModel) || selectedSummaryModel) : '自动多数投票'}
              </p>
            </div>
            <div className="rounded-xl bg-canvas/80 px-3 py-2">
              <p className="text-[11px] text-faint">图片视觉直答模型</p>
              <p className="mt-0.5 truncate font-medium text-ink">
                {selectedVisionModel ? (modelLookup.get(selectedVisionModel) || selectedVisionModel) : '自动选用已配视觉模型'}
              </p>
            </div>
            <div className="rounded-xl bg-canvas/80 px-3 py-2">
              <p className="text-[11px] text-faint">AI 助教 / 图谱模型</p>
              <p className="mt-0.5 truncate font-medium text-ink">
                {selectedAgentModel ? (modelLookup.get(selectedAgentModel) || selectedAgentModel) : '默认跟随首个答题模型'}
              </p>
            </div>
          </div>

          <div className="grid gap-4 lg:grid-cols-12">
            <aside className="h-fit space-y-2.5 rounded-2xl border border-line bg-white p-3.5 shadow-card lg:col-span-3">
              <div className="flex items-center justify-between px-1">
                <span className="flex items-center gap-1.5 text-xs font-semibold text-ink">
                  <Cpu className="h-3.5 w-3.5 text-brand" />
                  AI 供应商 ({draft.platforms.length})
                </span>
                <button
                  type="button"
                  onClick={handleAddPlatform}
                  className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-brand hover:bg-brand-soft"
                >
                  <Plus className="h-3.5 w-3.5" />
                  新增
                </button>
              </div>

              {draft.platforms.length > 6 && (
                <div className="relative">
                  <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-faint" />
                  <Input
                    value={platformQuery}
                    onChange={(e) => setPlatformQuery(e.target.value)}
                    placeholder="筛选平台..."
                    className="h-8 pl-8 text-xs"
                  />
                </div>
              )}

              <div className="max-h-[480px] space-y-1 overflow-y-auto pr-0.5">
                {filteredPlatforms.map((p) => {
                  const active = activePlatformId === p.id;
                  const enabledCount = (p.models || []).filter((m) => m.enabled !== false).length;
                  return (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => { setActivePlatformId(p.id); setShowKey(false); }}
                      className={`flex w-full items-center justify-between gap-2 rounded-xl px-2.5 py-2 text-left text-xs transition ${
                        active ? 'bg-brand-soft font-medium text-brand' : 'text-body hover:bg-soft hover:text-ink'
                      }`}
                    >
                      <div className="min-w-0 flex-1">
                        <p className="truncate">{p.displayName}</p>
                        <p className="text-[10px] text-faint tnum">
                          {enabledCount} 模型 {p.enabled === false ? '· 已停用' : ''}
                        </p>
                      </div>
                      <span
                        className={`shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-medium ${
                          p.enabled === false
                            ? 'bg-soft text-faint'
                            : p.apiKey
                              ? 'bg-success/10 text-success-ink'
                              : 'bg-soft text-faint'
                        }`}
                      >
                        {p.enabled === false ? '已停用' : p.apiKey ? '已配' : '未配'}
                      </span>
                    </button>
                  );
                })}
              </div>
            </aside>

            {activePlatform && (
              <div className="space-y-5 rounded-2xl border border-line bg-white p-5 shadow-card lg:col-span-9">
                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-3.5">
                  <div className="flex items-center gap-3">
                    <h3 className="text-base font-semibold text-ink">{activePlatform.displayName}</h3>
                    <ToggleSwitch
                      id={`plat-en-${activePlatform.id}`}
                      checked={activePlatform.enabled !== false}
                      onChange={(v) => updatePlatform(activePlatform.id, { enabled: v })}
                      label="启用平台"
                    />
                  </div>
                  {!activePlatform.isPreset && (
                    <Button size="sm" variant="ghost" onClick={() => setPlatformToDelete(activePlatform)}>
                      <Trash2 className="h-3.5 w-3.5 text-danger" />
                      删除平台
                    </Button>
                  )}
                </div>

                <div className="grid gap-3.5 sm:grid-cols-2">
                  <div className="space-y-1">
                    <Label>显示名称</Label>
                    <Input
                      value={activePlatform.displayName}
                      onChange={(e) => updatePlatform(activePlatform.id, { displayName: e.target.value, name: e.target.value })}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label>接口协议</Label>
                    <Select
                      value={activePlatform.apiProtocol}
                      onChange={(e) => updatePlatform(activePlatform.id, { apiProtocol: e.target.value })}
                    >
                      <option value="openai_chat">OpenAI Chat Completions (/chat/completions)</option>
                      <option value="openai_responses">OpenAI Responses (/responses)</option>
                      <option value="anthropic">Anthropic Messages (/messages)</option>
                    </Select>
                  </div>
                  <div className="space-y-1 sm:col-span-2">
                    <Label>Base URL</Label>
                    <Input
                      value={activePlatform.baseUrl}
                      onChange={(e) => updatePlatform(activePlatform.id, { baseUrl: e.target.value })}
                      placeholder="https://api.deepseek.com/v1"
                    />
                  </div>
                  <div className="space-y-1 sm:col-span-2">
                    <Label>API Key</Label>
                    <div className="relative">
                      <Input
                        ref={apiKeyInputRef}
                        type={showKey ? 'text' : 'password'}
                        value={activePlatform.apiKey || ''}
                        onChange={(e) => updatePlatform(activePlatform.id, { apiKey: e.target.value })}
                        placeholder="输入该平台的 API Key（如 sk-...）"
                        className="pr-10 font-mono text-xs"
                      />
                      <button
                        type="button"
                        onClick={() => setShowKey((v) => !v)}
                        aria-label={showKey ? '隐藏 API Key' : '显示 API Key'}
                        className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded p-1 text-faint hover:bg-soft hover:text-ink"
                      >
                        {showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                      </button>
                    </div>
                  </div>
                </div>

                {/* 模型列表：配 Key 后可直接勾选角色，无需跳回控制台 */}
                <div className="space-y-3 border-t border-line pt-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="text-xs font-semibold text-ink">模型列表与角色分配（{activePlatform.models.length}）</p>
                    <span className="text-[11px] text-faint">可直接点「测试」验证连通性，并就地分配为答题 / 复核 / 视觉 / 助教</span>
                  </div>

                  <div className="divide-y divide-line overflow-hidden rounded-xl border border-line">
                    {activePlatform.models.length === 0 ? (
                      <p className="py-6 text-center text-xs text-faint">暂无模型，可在下方添加</p>
                    ) : (
                      activePlatform.models.map((m) => {
                        const tr = testResults[m.id];
                        const expanded = expandedModelId === m.id;
                        const isText = selectedTextModels.includes(m.id);
                        const isSummary = selectedSummaryModel === m.id;
                        const isVision = selectedVisionModel === m.id;
                        const isAgent = selectedAgentModel === m.id;
                        const disabledModel = m.enabled === false || activePlatform.enabled === false;
                        return (
                          <div key={m.id} className="bg-white">
                            <div className="flex flex-wrap items-center justify-between gap-2 px-3.5 py-3 text-xs">
                              <div className="min-w-0 flex-1 space-y-1.5">
                                <div className="flex flex-wrap items-center gap-2">
                                  <ToggleSwitch
                                    id={`m-en-${m.id}`}
                                    checked={m.enabled !== false}
                                    onChange={(v) => handleUpdateModel(activePlatform.id, m.id, { enabled: v })}
                                  />
                                  <span className={`font-medium ${m.enabled === false ? 'text-faint line-through' : 'text-ink'}`}>
                                    {m.displayName}
                                  </span>
                                  <span className="rounded bg-canvas px-1.5 py-0.5 font-mono text-[11px] text-faint">
                                    {m.modelId}
                                  </span>
                                </div>

                                {/* 第一排：就地角色分配 */}
                                <div className="flex flex-wrap items-center gap-1.5 pl-11 text-[11px]">
                                  <button
                                    type="button"
                                    disabled={disabledModel}
                                    onClick={() => toggleRoleText(m.id)}
                                    title="设为并发基础答题模型（最多5个）"
                                    className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 font-medium transition disabled:opacity-45 ${
                                      isText
                                        ? 'border-brand bg-brand text-white shadow-xs'
                                        : 'border-line bg-white text-body hover:border-brand/40 hover:text-brand'
                                    }`}
                                  >
                                    {isText && <Check className="h-3 w-3" />}
                                    答题
                                  </button>
                                  <button
                                    type="button"
                                    disabled={disabledModel}
                                    onClick={() => toggleRoleSummary(m.id)}
                                    title="设为汇总复核模型"
                                    className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 font-medium transition disabled:opacity-45 ${
                                      isSummary
                                        ? 'border-indigo-500 bg-indigo-600 text-white shadow-xs'
                                        : 'border-line bg-white text-body hover:border-indigo-300 hover:text-indigo-700'
                                    }`}
                                  >
                                    {isSummary && <Check className="h-3 w-3" />}
                                    复核
                                  </button>
                                  {m.hasVision && (
                                    <button
                                      type="button"
                                      disabled={disabledModel}
                                      onClick={() => toggleRoleVision(m.id)}
                                      title="设为图片题视觉直答模型"
                                      className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 font-medium transition disabled:opacity-45 ${
                                        isVision
                                          ? 'border-emerald-600 bg-emerald-600 text-white shadow-xs'
                                          : 'border-line bg-white text-body hover:border-emerald-300 hover:text-emerald-700'
                                      }`}
                                    >
                                      {isVision && <Check className="h-3 w-3" />}
                                      视觉直答
                                    </button>
                                  )}
                                  <button
                                    type="button"
                                    disabled={disabledModel}
                                    onClick={() => toggleRoleAgent(m.id)}
                                    title="设为 AI 助教与知识图谱专用模型"
                                    className={`inline-flex items-center gap-1 rounded-md border px-2 py-0.5 font-medium transition disabled:opacity-45 ${
                                      isAgent
                                        ? 'border-amber-500 bg-amber-500 text-white shadow-xs'
                                        : 'border-line bg-white text-body hover:border-amber-300 hover:text-amber-700'
                                    }`}
                                  >
                                    {isAgent ? <Check className="h-3 w-3" /> : <Sparkles className="h-2.5 w-2.5" />}
                                    助教
                                  </button>

                                  <span className="mx-0.5 text-line">|</span>

                                  <button
                                    type="button"
                                    onClick={() => handleUpdateModel(activePlatform.id, m.id, { hasVision: !m.hasVision })}
                                    className={`rounded-md border px-2 py-0.5 transition ${
                                      m.hasVision
                                        ? 'border-indigo-200 bg-indigo-50 font-medium text-indigo-700'
                                        : 'border-line bg-canvas text-faint hover:text-ink'
                                    }`}
                                  >
                                    视觉多模态
                                  </button>
                                  <button
                                    type="button"
                                    onClick={() => handleUpdateModel(activePlatform.id, m.id, { enableThinking: !m.enableThinking })}
                                    className={`rounded-md border px-2 py-0.5 transition ${
                                      m.enableThinking
                                        ? 'border-brand/25 bg-brand-soft font-medium text-brand'
                                        : 'border-line bg-canvas text-faint hover:text-ink'
                                    }`}
                                  >
                                    深度思考
                                  </button>
                                  <button
                                    type="button"
                                    onClick={() => setExpandedModelId(expanded ? null : m.id)}
                                    className="inline-flex items-center gap-0.5 text-faint hover:text-ink"
                                  >
                                    高级参数
                                    {expanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                                  </button>
                                  {tr && (
                                    <span className={`inline-flex items-center gap-1 font-medium ${tr.ok ? 'text-success-ink' : 'text-danger'}`}>
                                      {tr.ok ? <CheckCircle2 className="h-3.5 w-3.5" /> : <XCircle className="h-3.5 w-3.5" />}
                                      {tr.ok ? `连通成功 (${tr.latencyMs}ms)` : tr.error}
                                    </span>
                                  )}
                                </div>
                              </div>

                              <div className="flex items-center gap-1.5">
                                <Button
                                  size="sm"
                                  variant="outline"
                                  disabled={testingModelId === m.id}
                                  onClick={() => void handleTestModel(activePlatform, m)}
                                >
                                  {testingModelId === m.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <PlugZap className="h-3.5 w-3.5" />}
                                  测试
                                </Button>
                                <button
                                  type="button"
                                  onClick={() => handleDeleteModel(activePlatform.id, m.id)}
                                  title="删除模型"
                                  className="rounded-lg p-1.5 text-faint hover:bg-soft hover:text-danger"
                                >
                                  <Trash2 className="h-3.5 w-3.5" />
                                </button>
                              </div>
                            </div>

                            {expanded && (
                              <div className="grid gap-3 border-t border-line bg-canvas/60 px-4 py-3 text-xs sm:grid-cols-3">
                                <div className="space-y-1">
                                  <Label>Temperature</Label>
                                  <Input
                                    type="number"
                                    step="0.1"
                                    min="0"
                                    max="2"
                                    className="h-8 text-xs"
                                    value={m.temperature ?? 0.2}
                                    onChange={(e) => handleUpdateModel(activePlatform.id, m.id, { temperature: Number(e.target.value) })}
                                  />
                                </div>
                                <div className="space-y-1">
                                  <Label>Max Tokens</Label>
                                  <Input
                                    type="number"
                                    step="256"
                                    min="256"
                                    max="65536"
                                    className="h-8 text-xs"
                                    value={m.maxTokens ?? 4096}
                                    onChange={(e) => handleUpdateModel(activePlatform.id, m.id, { maxTokens: Number(e.target.value) || 4096 })}
                                  />
                                </div>
                                <div className="space-y-1">
                                  <Label>思考强度</Label>
                                  <Select
                                    className="h-8 text-xs"
                                    value={m.thinkingEffort || 'medium'}
                                    onChange={(e) => handleUpdateModel(activePlatform.id, m.id, { thinkingEffort: e.target.value })}
                                  >
                                    <option value="low">低 (low)</option>
                                    <option value="medium">中 (medium)</option>
                                    <option value="high">高 (high)</option>
                                  </Select>
                                </div>
                              </div>
                            )}
                          </div>
                        );
                      })
                    )}
                  </div>

                  {/* 添加新模型表单 */}
                  <form onSubmit={handleAddModel} className="space-y-2.5 rounded-xl border border-dashed border-line bg-canvas/70 p-3.5">
                    <p className="text-xs font-medium text-body">向 {activePlatform.displayName} 添加新模型</p>
                    <div className="grid gap-2 sm:grid-cols-12">
                      <Input
                        className="h-9 text-xs sm:col-span-5"
                        placeholder="模型 ID（如 gpt-4.1-mini）"
                        value={newModelId}
                        onChange={(e) => setNewModelId(e.target.value)}
                      />
                      <Input
                        className="h-9 text-xs sm:col-span-4"
                        placeholder="显示名称（选填）"
                        value={newModelName}
                        onChange={(e) => setNewModelName(e.target.value)}
                      />
                      <Button type="submit" size="sm" variant="outline" className="h-9 sm:col-span-3" disabled={!newModelId.trim()}>
                        <Plus className="h-3.5 w-3.5" />
                        添加模型
                      </Button>
                    </div>
                    <div className="flex flex-wrap items-center gap-4 pt-0.5 text-xs">
                      <ToggleSwitch id="new-m-vision" checked={newModelVision} onChange={setNewModelVision} label="支持视觉图片题" />
                      <ToggleSwitch id="new-m-thinking" checked={newModelThinking} onChange={setNewModelThinking} label="支持深度思考" />
                    </div>
                  </form>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* 子页 2：联网搜题增强 */}
      {section === 'search' && (
        <section className="max-w-3xl space-y-5 rounded-2xl border border-line bg-white p-6 shadow-card">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-4">
            <div className="flex items-center gap-2.5">
              <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-soft text-brand">
                <Globe className="h-4 w-4" />
              </div>
              <div>
                <h3 className="text-sm font-semibold text-ink">联网搜题增强</h3>
                <p className="text-xs text-faint">AI 答题前先实时检索网络资料，显著提升冷门题与时政题准确率</p>
              </div>
            </div>
            <ToggleSwitch
              id="search-enabled-toggle"
              checked={Boolean(draft.search?.enabled)}
              onChange={(v) => setDraft({ ...draft, search: { ...draft.search, enabled: v } })}
              label="启用联网检索"
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label>搜索引擎供应商</Label>
              <Select
                value={draft.search?.provider || 'tavily'}
                onChange={(e) => setDraft({ ...draft, search: { ...draft.search, provider: e.target.value } })}
              >
                {effectiveSearchProviders.map((sp) => (
                  <option key={sp.id} value={sp.id}>{sp.name}</option>
                ))}
              </Select>
              {currentSearchProvider?.website && (
                <a
                  href={currentSearchProvider.website}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 text-[11px] text-brand hover:underline"
                >
                  前往 {currentSearchProvider.name} 获取 API Key
                  <ExternalLink className="h-3 w-3" />
                </a>
              )}
            </div>

            {currentSearchProvider && (
              <div className="space-y-1.5">
                <Label>{currentSearchProvider.name} API Key</Label>
                <div className="relative">
                  <Input
                    type={showSearchKey ? 'text' : 'password'}
                    value={draft.search?.[currentSearchProvider.keyField] || ''}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        search: { ...draft.search, [currentSearchProvider.keyField]: e.target.value },
                      })
                    }
                    placeholder="填写搜索服务 API Key"
                    className="pr-10 font-mono text-xs"
                  />
                  <button
                    type="button"
                    onClick={() => setShowSearchKey((v) => !v)}
                    aria-label={showSearchKey ? '隐藏搜索 Key' : '显示搜索 Key'}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded p-1 text-faint hover:bg-soft hover:text-ink"
                  >
                    {showSearchKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
              </div>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-3 pt-1">
            <Button size="sm" variant="outline" onClick={handleTestSearch} disabled={testingSearch}>
              {testingSearch ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Globe className="h-3.5 w-3.5" />}
              测试搜索连通
            </Button>
            {searchTestResult && (
              <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${searchTestResult.status === 'success' ? 'text-success-ink' : 'text-danger'}`}>
                {searchTestResult.status === 'success' ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
                {searchTestResult.status === 'success'
                  ? `成功返回 ${searchTestResult.results?.length || 0} 条结果 (${searchTestResult.durationMs}ms)`
                  : searchTestResult.error || '测试失败'}
              </span>
            )}
          </div>

          {searchTestResult?.results?.length > 0 && (
            <div className="space-y-1.5 rounded-xl border border-line bg-canvas p-3 text-xs">
              <p className="font-semibold text-ink">搜索返回预览</p>
              {searchTestResult.results.slice(0, 3).map((r, i) => (
                <div key={i} className="border-t border-line/70 pt-1.5">
                  <p className="font-medium text-ink">{r.title}</p>
                  <p className="line-clamp-2 text-faint">{r.snippet}</p>
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      {/* 子页 3：双题库与协同引擎 */}
      {section === 'engine' && (
        <section className="max-w-4xl space-y-5 rounded-2xl border border-line bg-white p-6 shadow-card">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-4">
            <div className="flex items-center gap-2.5">
              <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand-soft text-brand">
                <Sliders className="h-4 w-4" />
              </div>
              <div>
                <h3 className="text-sm font-semibold text-ink">双题库协同与高级引擎策略</h3>
                <p className="text-xs text-faint">控制超星刷课时的查题顺序、超时重试与本地题库沉淀策略</p>
              </div>
            </div>
            <ToggleSwitch
              id="engine-enabled-toggle"
              checked={Boolean(draft.engine?.enabled)}
              onChange={(v) => setDraft({ ...draft, engine: { ...draft.engine, enabled: v } })}
              label="刷课时启用 AI 协同"
            />
          </div>

          {/* 查题优先级卡片选择 */}
          <div className="space-y-2">
            <Label>双题库查题优先级</Label>
            <div className="grid gap-2.5 sm:grid-cols-2">
              {PRIORITY_CARDS.map((card) => {
                const active = (draft.engine?.priority || 'local_ocs_ai') === card.id;
                return (
                  <button
                    key={card.id}
                    type="button"
                    onClick={() => setDraft({ ...draft, engine: { ...draft.engine, priority: card.id } })}
                    className={`rounded-xl border p-3.5 text-left transition ${
                      active
                        ? 'border-brand bg-brand-soft/60 ring-2 ring-brand/15'
                        : 'border-line bg-canvas/40 hover:border-faint/50 hover:bg-canvas'
                    }`}
                  >
                    <p className={`text-xs font-semibold ${active ? 'text-brand' : 'text-ink'}`}>{card.title}</p>
                    <p className="mt-1 text-[11px] leading-relaxed text-faint">{card.desc}</p>
                  </button>
                );
              })}
            </div>
          </div>

          <div className="grid gap-4 sm:grid-cols-3">
            <div className="space-y-1">
              <Label>新答案默认入库文件夹</Label>
              <Select
                value={String(draft.engine?.defaultFolderId ?? 0)}
                onChange={(e) => setDraft({ ...draft, engine: { ...draft.engine, defaultFolderId: Number(e.target.value) } })}
              >
                {folders.length ? folders.map((f) => <option key={f.id} value={f.id}>{f.name}</option>) : <option value="0">默认文件夹</option>}
              </Select>
            </div>
            <div className="space-y-1">
              <Label>AI 助教 / 图谱专用模型</Label>
              <Select
                value={draft.selectedAgentModel || ''}
                onChange={(e) => setDraft({ ...draft, selectedAgentModel: e.target.value || null })}
              >
                <option value="">默认使用首个基础答题模型</option>
                {allModels.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.hasKey ? '' : '[缺Key] '}{m.platformName} / {m.displayName}
                  </option>
                ))}
              </Select>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <Label>超时 (秒)</Label>
                <Input
                  type="number"
                  min={5}
                  max={300}
                  value={draft.engine?.modelTimeout ?? 45}
                  onChange={(e) => setDraft({ ...draft, engine: { ...draft.engine, modelTimeout: Number(e.target.value) || 45 } })}
                />
              </div>
              <div className="space-y-1">
                <Label>重试次数</Label>
                <Input
                  type="number"
                  min={0}
                  max={5}
                  value={draft.engine?.modelRetryCount ?? 2}
                  onChange={(e) => setDraft({ ...draft, engine: { ...draft.engine, modelRetryCount: Number(e.target.value) || 0 } })}
                />
              </div>
            </div>
          </div>

          <div className="grid gap-3 rounded-xl border border-line bg-canvas/60 p-4 sm:grid-cols-3">
            <ToggleSwitch
              id="engine-autosave"
              checked={draft.engine?.autoSaveToBank !== false}
              onChange={(v) => setDraft({ ...draft, engine: { ...draft.engine, autoSaveToBank: v } })}
              label="新答案自动入库"
              description="查到的新题自动沉淀到本地"
            />
            <ToggleSwitch
              id="engine-samecheck"
              checked={draft.engine?.enableSameQuestionCheck !== false}
              onChange={(v) => setDraft({ ...draft, engine: { ...draft.engine, enableSameQuestionCheck: v } })}
              label="AI 智能同题判重"
              description="识别题干微调变体复用答案"
            />
            <ToggleSwitch
              id="engine-vision"
              checked={draft.engine?.enableVisionForImages !== false}
              onChange={(v) => setDraft({ ...draft, engine: { ...draft.engine, enableVisionForImages: v } })}
              label="图片题视觉直答"
              description="含图题目自动路由视觉模型"
            />
          </div>
        </section>
      )}

      <ConfirmDialog
        open={Boolean(platformToDelete)}
        title={`删除自定义平台「${platformToDelete?.displayName || ''}」`}
        description="确认移除该平台及其下的模型配置吗？需点击「保存全部设置」后生效。"
        confirmText="移除平台"
        destructive
        onCancel={() => setPlatformToDelete(null)}
        onConfirm={handleConfirmDeletePlatform}
      />
    </div>
  );
}
