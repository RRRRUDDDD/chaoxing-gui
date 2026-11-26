import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import Button from '../ui/Button';
import { aiApi } from '../../api/aiApi';
import DashboardTab from './DashboardTab';
import QuestionBankTab from './QuestionBankTab';
import PracticeTab from './PracticeTab';
import StudyGraphTab from './StudyGraphTab';
import AgentChatTab from './AgentChatTab';
import ModelSettingsTab from './ModelSettingsTab';
import QuickTutorDrawer from './QuickTutorDrawer';
import { ToastViewport } from './aiShared';
import {
  ArrowLeft, LayoutDashboard, Database, ClipboardCheck, BrainCircuit,
  MessageSquare, Settings2, Sparkles, Loader2, RefreshCw, KeyRound, Save,
} from 'lucide-react';

const NAV_GROUPS = [
  {
    title: '查题与题库',
    items: [
      { id: 'dashboard', label: '控制台与查题', desc: '在线测试 · 调度 · 日志', icon: LayoutDashboard },
      { id: 'questions', label: '本地题库管理', desc: '目录 · 速改错题 · 导入导出', icon: Database },
    ],
  },
  {
    title: '刷题与辅导',
    items: [
      { id: 'practice', label: '智能刷题练习', desc: '薄弱优先 · 原地纠错 · 快捷键', icon: ClipboardCheck },
      { id: 'study', label: '知识图谱复习', desc: '图谱构建 · 艾宾浩斯连贯复习', icon: BrainCircuit },
      { id: 'agent', label: 'AI 助教对话', desc: '多会话管理 · 深度辅导', icon: MessageSquare },
    ],
  },
  {
    title: '系统与模型',
    items: [
      { id: 'settings', label: '模型与搜题设置', desc: 'Key · 角色分配 · 搜题与协同', icon: Settings2 },
    ],
  },
];

export default function AIWorkspace({
  onBack,
  backLabel = '返回超星学习通',
  initialTab = 'dashboard',
  chaoxingContext = null,
  onAgentEffects,
  onAiEngineChange,
  onViewProgress,
}) {
  const [activeTab, setActiveTab] = useState(initialTab);
  const [visitedTabs, setVisitedTabs] = useState(() => new Set([initialTab]));
  const [tabIntents, setTabIntents] = useState({});
  const [dataVersion, setDataVersion] = useState(0);

  const [config, setConfig] = useState(null);
  const [searchProviders, setSearchProviders] = useState([]);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [errorMsg, setErrorMsg] = useState('');
  const [settingsDirty, setSettingsDirty] = useState(false);

  // Global slide-over Quick AI Tutor Drawer
  const [tutorOpen, setTutorOpen] = useState(false);
  const [tutorRequest, setTutorRequest] = useState(null);

  const [toasts, setToasts] = useState([]);
  const toastSeq = useRef(0);
  const toastTimers = useRef(new Map());

  const dismissToast = useCallback((id) => {
    const timer = toastTimers.current.get(id);
    if (timer) {
      clearTimeout(timer);
      toastTimers.current.delete(id);
    }
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const pushToast = useCallback((message, type = 'info') => {
    if (!message) return;
    const id = ++toastSeq.current;
    setToasts((prev) => [...prev.slice(-3), { id, message, type }]);
    const duration = type === 'error' ? 6000 : 3500;
    const timer = setTimeout(() => {
      toastTimers.current.delete(id);
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, duration);
    toastTimers.current.set(id, timer);
  }, []);

  useEffect(() => {
    const timers = toastTimers.current;
    return () => {
      timers.forEach((t) => clearTimeout(t));
      timers.clear();
    };
  }, []);

  const notifyAiEngine = useCallback((cfg) => {
    if (!cfg?.engine || !onAiEngineChange) return;
    onAiEngineChange({
      enabled: Boolean(cfg.engine.enabled),
      priority: cfg.engine.priority || 'local_ocs_ai',
      _ts: Date.now(),
    });
  }, [onAiEngineChange]);

  const loadAll = useCallback(async (silent = false) => {
    if (!silent) setErrorMsg('');
    else setRefreshing(true);
    try {
      const [cfgRes, statsRes] = await Promise.all([aiApi.getConfig(), aiApi.getStats()]);
      setConfig(cfgRes.config);
      notifyAiEngine(cfgRes.config);
      setSearchProviders(cfgRes.searchProviders || []);
      setStats(statsRes);
      setDataVersion((v) => v + 1);
    } catch (err) {
      setErrorMsg(err.message || '无法加载 AI 服务数据');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [notifyAiEngine]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [cfgRes, statsRes] = await Promise.all([aiApi.getConfig(), aiApi.getStats()]);
        if (cancelled) return;
        setConfig(cfgRes.config);
        setSearchProviders(cfgRes.searchProviders || []);
        setStats(statsRes);
      } catch (err) {
        if (!cancelled) setErrorMsg(err.message || '无法加载 AI 服务数据');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const handleConfigSaved = useCallback((res) => {
    if (res?.config) {
      setConfig(res.config);
      notifyAiEngine(res.config);
    }
  }, [notifyAiEngine]);

  const refreshStats = useCallback(async () => {
    setDataVersion((v) => v + 1);
    try {
      const s = await aiApi.getStats();
      setStats(s);
    } catch { /* ignore */ }
  }, []);

  const navigateTo = useCallback((tab, intent = null) => {
    setActiveTab(tab);
    setVisitedTabs((prev) => {
      if (prev.has(tab)) return prev;
      const next = new Set(prev);
      next.add(tab);
      return next;
    });
    if (intent) {
      setTabIntents((prev) => ({ ...prev, [tab]: { ...intent, _ts: Date.now() } }));
    }
  }, []);

  const askQuickTutor = useCallback((req = {}) => {
    if (activeTab === 'agent') {
      // Already on full Agent tab: pass prefill directly instead of double drawer
      setTabIntents((prev) => ({
        ...prev,
        agent: { prefill: req.prompt || '', subjectId: req.subjectId, _ts: Date.now() },
      }));
      return;
    }
    setTutorRequest({ ...req, _ts: Date.now() });
    setTutorOpen(true);
  }, [activeTab]);

  const handleWorkspaceAgentEffects = useCallback((effects) => {
    if (!Array.isArray(effects) || !effects.length) return;
    if (effects.some((e) => e.type === 'config_changed')) {
      void loadAll(true);
    } else if (effects.some((e) => e.type === 'bank_changed' || e.type === 'graph_changed')) {
      void refreshStats();
    }
    const started = effects.find((e) => e.type === 'task_started');
    if (started) {
      pushToast('Agent 已启动学习通任务，可点击「查看任务进度」跟踪执行', 'success');
    }
    const stopped = effects.find((e) => e.type === 'task_stopped');
    if (stopped) {
      pushToast('Agent 已提交停止学习通任务请求', 'info');
    }
    onAgentEffects?.(effects);
  }, [loadAll, refreshStats, pushToast, onAgentEffects]);

  const configuredPlatformCount = useMemo(
    () => (config?.platforms || []).filter((p) => p.enabled !== false && Boolean(p.apiKey)).length,
    [config]
  );

  const renderBadge = (id, active) => {
    if (id === 'questions') {
      if ((stats?.pendingCorrections || 0) > 0) {
        return (
          <span
            className={`rounded-full px-1.5 py-0.5 text-[10px] font-semibold tnum ${
              active ? 'bg-white/20 text-white' : 'bg-warning/15 text-warning-ink'
            }`}
            title={`${stats.pendingCorrections} 道待修正错题`}
          >
            {stats.pendingCorrections}
          </span>
        );
      }
      if ((stats?.totalQuestions || 0) > 0) {
        return (
          <span className={`text-[11px] tnum ${active ? 'text-white/80' : 'text-faint'}`}>
            {stats.totalQuestions}
          </span>
        );
      }
    }
    if (id === 'study' && (stats?.knowledgeNodeCount || 0) > 0) {
      return (
        <span className={`text-[11px] tnum ${active ? 'text-white/80' : 'text-faint'}`}>
          {stats.knowledgeNodeCount}
        </span>
      );
    }
    if (id === 'settings') {
      if (settingsDirty) {
        return (
          <span
            className={`rounded-full px-1.5 py-0.5 text-[10px] font-medium ${
              active ? 'bg-white/20 text-white' : 'bg-warning/20 text-warning-ink'
            }`}
            title="有未保存的设置修改"
          >
            未保存
          </span>
        );
      }
      if (config && configuredPlatformCount === 0) {
        return (
          <span
            className={`rounded-full px-1.5 py-0.5 text-[10px] font-medium ${
              active ? 'bg-white/20 text-white' : 'bg-warning/15 text-warning-ink'
            }`}
          >
            待配Key
          </span>
        );
      }
    }
    return null;
  };

  return (
    <div className="flex min-h-screen flex-col bg-canvas lg:h-screen lg:overflow-hidden">
      {/* 统一顶栏 */}
      <header className="sticky top-0 z-20 shrink-0 border-b border-line bg-white/95 backdrop-blur">
        <div className="flex h-14 items-center justify-between gap-3 px-4 lg:px-5">
          <div className="flex min-w-0 items-center gap-3">
            <Button variant="outline" size="sm" onClick={onBack}>
              <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
              <span>{backLabel}</span>
            </Button>
            <div className="hidden h-5 w-px bg-line sm:block" aria-hidden="true" />
            <div className="flex min-w-0 items-center gap-2.5">
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-brand shadow-xs">
                <Sparkles className="h-4 w-4 text-white" aria-hidden="true" />
              </div>
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold leading-tight text-ink">AI 题库与学习工作台</p>
              </div>
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-2 text-xs">
            {settingsDirty && activeTab !== 'settings' && (
              <button
                type="button"
                onClick={() => navigateTo('settings')}
                className="inline-flex items-center gap-1 rounded-full border border-warning/35 bg-warning/10 px-2.5 py-1 text-[11px] font-medium text-warning-ink transition hover:bg-warning/20"
                title="设置页有未保存修改，点击返回保存"
              >
                <Save className="h-3 w-3" />
                <span>有未保存设置</span>
              </button>
            )}
            {config?.engine?.enabled && (
              <button
                type="button"
                onClick={() => navigateTo('settings', { section: 'engine' })}
                className="hidden rounded-full border border-brand/15 bg-brand-soft px-2.5 py-0.5 text-[11px] font-medium text-brand transition hover:border-brand/35 md:inline-flex"
                title="点击管理双题库与刷课协同策略"
              >
                刷课协同已启用
              </button>
            )}
            <Button
              variant="outline"
              size="sm"
              onClick={() => void loadAll(true)}
              disabled={refreshing}
              title="刷新工作台状态"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? 'animate-spin' : ''}`} />
              <span className="hidden sm:inline">刷新</span>
            </Button>
          </div>
        </div>
      </header>

      {/* 主体：桌面端左侧固定分组导航栏 + 右侧独立滚动主区；移动端顶部横向导航 */}
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <aside className="shrink-0 border-b border-line bg-white lg:flex lg:w-60 lg:flex-col lg:border-b-0 lg:border-r">
          {/* 响应式单份导航：移动端横向滚动，桌面端按工作流分组 */}
          <nav
            aria-label="AI 模块导航"
            className="flex gap-1 overflow-x-auto px-3 py-2 lg:flex-1 lg:flex-col lg:gap-4 lg:overflow-y-auto lg:p-3"
          >
            {NAV_GROUPS.map((group) => (
              <div key={group.title} className="contents lg:block lg:space-y-1">
                <p className="hidden px-2.5 text-[10px] font-semibold uppercase tracking-wider text-faint/80 lg:block">
                  {group.title}
                </p>
                {group.items.map((item) => {
                  const active = activeTab === item.id;
                  const Icon = item.icon;
                  return (
                    <button
                      key={item.id}
                      type="button"
                      aria-label={item.label}
                      aria-current={active ? 'page' : undefined}
                      onClick={() => navigateTo(item.id)}
                      className={`group flex shrink-0 items-center gap-2.5 rounded-xl px-3 py-2 text-left transition lg:w-full lg:py-2.5 ${
                        active
                          ? 'bg-brand text-white shadow-sm'
                          : 'text-body hover:bg-soft hover:text-ink'
                      }`}
                    >
                      <Icon
                        className={`h-4 w-4 shrink-0 ${active ? 'text-white' : 'text-faint group-hover:text-ink'}`}
                        aria-hidden="true"
                      />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-xs font-medium leading-tight lg:text-[13px]">{item.label}</p>
                        <p className={`mt-0.5 hidden truncate text-[10px] leading-tight lg:block ${active ? 'text-white/75' : 'text-faint'}`}>
                          {item.desc}
                        </p>
                      </div>
                      <span className="hidden lg:inline-flex">{renderBadge(item.id, active)}</span>
                    </button>
                  );
                })}
              </div>
            ))}
          </nav>

          {/* 桌面端侧边栏底部快捷状态卡（直接可操作） */}
          <div className="hidden space-y-2 border-t border-line bg-canvas/50 p-3 text-[11px] lg:block">
            <button
              type="button"
              onClick={() => navigateTo('settings', { section: 'platforms' })}
              className={`flex w-full items-center justify-between rounded-lg border px-2.5 py-1.5 text-left transition ${
                configuredPlatformCount > 0
                  ? 'border-line bg-white text-body hover:border-brand/40'
                  : 'border-warning/30 bg-warning/5 text-warning-ink hover:bg-warning/10'
              }`}
            >
              <span className="flex items-center gap-1.5 truncate font-medium">
                <KeyRound className="h-3.5 w-3.5 shrink-0" />
                {configuredPlatformCount > 0 ? `已配置 ${configuredPlatformCount} 个 AI 平台` : '点击配置 AI Key'}
              </span>
            </button>
          </div>
        </aside>

        {/* 可滚动工作主区：保持已访问模块挂载，切换不丢任何状态 */}
        <main className="min-w-0 flex-1 overflow-y-auto px-4 py-5 lg:px-6">
          <div className="mx-auto w-full max-w-[1440px]">
            {loading ? (
              <div className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-line bg-white py-24 text-sm text-faint shadow-card">
                <Loader2 className="h-6 w-6 animate-spin text-brand" />
                <span>正在初始化 AI 工作台...</span>
              </div>
            ) : errorMsg ? (
              <div role="alert" className="mx-auto max-w-md space-y-3 rounded-2xl border border-danger/25 bg-white p-8 text-center shadow-card">
                <p className="text-sm font-medium text-danger">{errorMsg}</p>
                <Button size="sm" onClick={() => void loadAll()}>重试加载</Button>
              </div>
            ) : (
              <>
                {visitedTabs.has('dashboard') && (
                  <div hidden={activeTab !== 'dashboard'}>
                    <DashboardTab
                      isActive={activeTab === 'dashboard'}
                      config={config}
                      stats={stats}
                      configuredPlatformCount={configuredPlatformCount}
                      onRefreshAll={loadAll}
                      onConfigSaved={handleConfigSaved}
                      onNavigate={navigateTo}
                      onAskTutor={askQuickTutor}
                      onToast={pushToast}
                    />
                  </div>
                )}
                {visitedTabs.has('questions') && (
                  <div hidden={activeTab !== 'questions'}>
                    <QuestionBankTab
                      isActive={activeTab === 'questions'}
                      intent={tabIntents.questions}
                      pendingCount={stats?.pendingCorrections || 0}
                      dataVersion={dataVersion}
                      onStatsChanged={refreshStats}
                      onNavigate={navigateTo}
                      onAskTutor={askQuickTutor}
                      onToast={pushToast}
                    />
                  </div>
                )}
                {visitedTabs.has('practice') && (
                  <div hidden={activeTab !== 'practice'}>
                    <PracticeTab
                      isActive={activeTab === 'practice' && !tutorOpen}
                      intent={tabIntents.practice}
                      dataVersion={dataVersion}
                      onStatsChanged={refreshStats}
                      onNavigate={navigateTo}
                      onAskTutor={askQuickTutor}
                      onToast={pushToast}
                    />
                  </div>
                )}
                {visitedTabs.has('study') && (
                  <div hidden={activeTab !== 'study'}>
                    <StudyGraphTab
                      isActive={activeTab === 'study'}
                      intent={tabIntents.study}
                      dataVersion={dataVersion}
                      onStatsChanged={refreshStats}
                      onAskTutor={askQuickTutor}
                      onToast={pushToast}
                    />
                  </div>
                )}
                {visitedTabs.has('agent') && (
                  <div hidden={activeTab !== 'agent'}>
                    <AgentChatTab
                      isActive={activeTab === 'agent'}
                      intent={tabIntents.agent}
                      config={config}
                      dataVersion={dataVersion}
                      chaoxingContext={chaoxingContext}
                      onAgentEffects={handleWorkspaceAgentEffects}
                      onViewProgress={onViewProgress}
                      onNavigate={navigateTo}
                      onStatsChanged={refreshStats}
                      onToast={pushToast}
                    />
                  </div>
                )}
                {visitedTabs.has('settings') && (
                  <div hidden={activeTab !== 'settings'}>
                    <ModelSettingsTab
                      isActive={activeTab === 'settings' && !tutorOpen}
                      config={config}
                      searchProviders={searchProviders}
                      intent={tabIntents.settings}
                      onConfigSaved={handleConfigSaved}
                      onDirtyChange={setSettingsDirty}
                      onToast={pushToast}
                    />
                  </div>
                )}
              </>
            )}
          </div>
        </main>
      </div>

      {/* 全局侧滑快捷 AI 助教抽屉 */}
      <QuickTutorDrawer
        open={tutorOpen}
        request={tutorRequest}
        config={config}
        chaoxingContext={chaoxingContext}
        onAgentEffects={handleWorkspaceAgentEffects}
        onViewProgress={onViewProgress ? () => { setTutorOpen(false); onViewProgress(); } : undefined}
        onNavigate={(tab) => { setTutorOpen(false); navigateTo(tab); }}
        onClose={() => setTutorOpen(false)}
        onOpenFullAgent={(sessionId) => navigateTo('agent', sessionId ? { sessionId } : null)}
        onStatsChanged={refreshStats}
        onToast={pushToast}
      />

      <ToastViewport toasts={toasts} onDismiss={dismissToast} />
    </div>
  );
}
