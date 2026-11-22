import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import Button from '../ui/Button';
import Input from '../ui/Input';
import Label from '../ui/Label';
import Select from '../ui/Select';
import { aiApi } from '../../api/aiApi';
import {
  QUESTION_TYPE_OPTIONS, getQuestionTypeMeta, MasteryDots, MarkdownContent,
  EmptyState, ToggleSwitch,
} from './aiShared';
import {
  CheckCircle2, XCircle, Sparkles, AlertTriangle, ArrowLeft, ArrowRight, RefreshCw,
  Loader2, History, SkipForward, MessageSquare, Keyboard, ClipboardCheck, Wrench, Check,
} from 'lucide-react';

const PRACTICE_MODES = [
  { id: 'smart', label: '智能薄弱' },
  { id: 'wrong', label: '错题专练' },
  { id: 'unmastered', label: '未掌握' },
  { id: 'random', label: '随机抽题' },
];

function parseOptionLines(optionsText) {
  return String(optionsText || '')
    .split(/\r?\n/)
    .map((ln) => ln.trim())
    .filter(Boolean);
}

function stripOptionPrefix(opt) {
  return opt.replace(/^[A-Za-z][.、．\s]+/, '').trim() || opt;
}

function normalizeToken(s) {
  return String(s || '').trim().toLowerCase().replace(/\s+/g, '');
}

function isOptionInAnswer(optRaw, optStripped, idx, answerStr) {
  const tokens = String(answerStr || '')
    .split(/###|\n/)
    .map((t) => normalizeToken(t))
    .filter(Boolean);
  if (!tokens.length) return false;
  const letter = String.fromCharCode(65 + idx).toLowerCase();
  const nRaw = normalizeToken(optRaw);
  const nStripped = normalizeToken(optStripped);
  return tokens.some((t) => t === nStripped || t === nRaw || t === letter);
}

const emptyRecord = () => ({
  singleAnswer: '',
  multiAnswers: [],
  textAnswer: '',
  submitted: null,
  aiExplain: '',
});

export default function PracticeTab({
  isActive = true,
  intent,
  dataVersion = 0,
  onStatsChanged,
  onNavigate,
  onAskTutor,
  onToast,
}) {
  const [folders, setFolders] = useState([]);
  const [folderId, setFolderId] = useState(() => (intent?.folderId !== undefined ? String(intent.folderId) : '-1'));
  const [mode, setMode] = useState(() => intent?.mode || 'smart');
  const [qType, setQType] = useState(() => intent?.questionType || 'all');
  const [queue, setQueue] = useState([]);
  const [index, setIndex] = useState(0);
  const [loading, setLoading] = useState(false);

  // Per-question records keyed by queue index so user can go back/forth freely
  const [records, setRecords] = useState({});
  const [submitting, setSubmitting] = useState(false);
  const [explaining, setExplaining] = useState(false);
  const [autoAdvance, setAutoAdvance] = useState(false);

  // Inline in-place correction state (no jumping to QuestionBank!)
  const [showCorrection, setShowCorrection] = useState(false);
  const [fixAnswer, setFixAnswer] = useState('');
  const [fixExplanation, setFixExplanation] = useState('');
  const [savingFix, setSavingFix] = useState(false);

  const [history, setHistory] = useState({ total: 0, correct: 0, accuracy: 0, items: [] });
  const [showHistory, setShowHistory] = useState(false);
  const [historyFilter, setHistoryFilter] = useState('all');
  const [errorMsg, setErrorMsg] = useState('');

  const [prevIntentTs, setPrevIntentTs] = useState(intent?._ts || null);
  const syncedFoldersVerRef = useRef(dataVersion);
  const autoAdvanceTimerRef = useRef(null);

  const cancelAutoAdvance = useCallback(() => {
    if (autoAdvanceTimerRef.current) {
      clearTimeout(autoAdvanceTimerRef.current);
      autoAdvanceTimerRef.current = null;
    }
  }, []);

  useEffect(() => () => cancelAutoAdvance(), [cancelAutoAdvance]);

  // React to navigation intents (e.g., from QuestionBank "刷当前目录")
  if (intent?._ts && intent._ts !== prevIntentTs) {
    setPrevIntentTs(intent._ts);
    setShowHistory(false);
    if (intent.folderId !== undefined) setFolderId(String(intent.folderId));
    if (intent.mode) setMode(intent.mode);
    if (intent.questionType) setQType(intent.questionType);
  }

  const loadHistory = useCallback(async () => {
    const res = await aiApi.getPracticeHistory(40);
    setHistory(res || { total: 0, correct: 0, accuracy: 0, items: [] });
  }, []);

  const loadQueue = useCallback(async () => {
    cancelAutoAdvance();
    setLoading(true);
    setErrorMsg('');
    setShowCorrection(false);
    try {
      const res = await aiApi.getPracticeQueue({
        folderId: Number(folderId) >= 0 ? Number(folderId) : null,
        questionType: qType,
        mode,
        limit: 30,
      });
      setQueue(res.items || []);
      setIndex(0);
      setRecords({});
    } catch (err) {
      setErrorMsg(err.message);
    } finally {
      setLoading(false);
    }
  }, [folderId, mode, qType, cancelAutoAdvance]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([aiApi.listFolders(), aiApi.getPracticeHistory(40)])
      .then(([foldRes, histRes]) => {
        if (cancelled) return;
        setFolders(foldRes.items || []);
        setHistory(histRes || { total: 0, correct: 0, accuracy: 0, items: [] });
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  // Refresh folder dropdown softly when returning after folder edits elsewhere
  useEffect(() => {
    if (isActive && dataVersion !== syncedFoldersVerRef.current) {
      syncedFoldersVerRef.current = dataVersion;
      aiApi.listFolders().then((res) => setFolders(res.items || [])).catch(() => {});
    }
  }, [isActive, dataVersion]);

  useEffect(() => {
    let cancelled = false;
    cancelAutoAdvance();
    aiApi.getPracticeQueue({
      folderId: Number(folderId) >= 0 ? Number(folderId) : null,
      questionType: qType,
      mode,
      limit: 30,
    })
      .then((res) => {
        if (cancelled) return;
        setShowCorrection(false);
        setQueue(res.items || []);
        setIndex(0);
        setRecords({});
        setErrorMsg('');
      })
      .catch((err) => { if (!cancelled) setErrorMsg(err.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [folderId, mode, qType, cancelAutoAdvance]);

  const current = queue[index] || null;
  const currentRec = records[index] || emptyRecord();
  const { singleAnswer, multiAnswers, textAnswer, submitted, aiExplain } = currentRec;

  const updateCurrentRec = useCallback((updater) => {
    setRecords((prev) => {
      const base = prev[index] || emptyRecord();
      const next = typeof updater === 'function' ? updater(base) : { ...base, ...updater };
      return { ...prev, [index]: next };
    });
  }, [index]);

  const sessionScore = useMemo(() => {
    let correct = 0;
    let wrong = 0;
    Object.values(records).forEach((r) => {
      if (r.submitted) {
        if (r.submitted.isCorrect) correct += 1;
        else wrong += 1;
      }
    });
    return { correct, wrong, answered: correct + wrong };
  }, [records]);

  const optionLines = useMemo(() => parseOptionLines(current?.options), [current]);
  const isMultiple = current?.questionType === 'multiple';
  const isJudgement = current?.questionType === 'judgement';
  const hasChoices = optionLines.length > 0 && (current?.questionType === 'single' || isMultiple || !current?.questionType);

  const currentUserAnswer = useCallback(() => {
    if (isJudgement) return singleAnswer;
    if (hasChoices && isMultiple) return multiAnswers.join('###');
    if (hasChoices) return singleAnswer;
    return textAnswer.trim();
  }, [isJudgement, singleAnswer, hasChoices, isMultiple, multiAnswers, textAnswer]);

  const goToIndex = useCallback((nextIdx) => {
    cancelAutoAdvance();
    setShowCorrection(false);
    setIndex(nextIdx);
  }, [cancelAutoAdvance]);

  const handleNext = useCallback(() => {
    cancelAutoAdvance();
    setShowCorrection(false);
    if (index + 1 < queue.length) {
      setIndex((i) => i + 1);
    } else {
      void loadQueue();
    }
  }, [index, queue.length, loadQueue, cancelAutoAdvance]);

  const handlePrev = useCallback(() => {
    cancelAutoAdvance();
    setShowCorrection(false);
    if (index > 0) setIndex((i) => i - 1);
  }, [index, cancelAutoAdvance]);

  const handleSubmit = useCallback(async () => {
    if (!current || submitting || submitted) return;
    const ans = currentUserAnswer();
    if (!ans) return;
    setSubmitting(true);
    setErrorMsg('');
    try {
      const res = await aiApi.submitPractice({ questionId: current.id, userAnswer: ans });
      updateCurrentRec({ submitted: res });
      await loadHistory();
      onStatsChanged?.();
      if (autoAdvance && res.isCorrect && index + 1 < queue.length) {
        cancelAutoAdvance();
        autoAdvanceTimerRef.current = setTimeout(() => {
          setIndex((i) => (i + 1 < queue.length ? i + 1 : i));
        }, 900);
      }
    } catch (err) {
      setErrorMsg(err.message);
    } finally {
      setSubmitting(false);
    }
  }, [
    current, submitting, submitted, currentUserAnswer, updateCurrentRec,
    loadHistory, onStatsChanged, autoAdvance, index, queue.length, cancelAutoAdvance,
  ]);

  const handleExplain = useCallback(async () => {
    if (!current || explaining) return;
    cancelAutoAdvance();
    setExplaining(true);
    setErrorMsg('');
    try {
      const res = await aiApi.explainQuestion(current.id, currentUserAnswer());
      updateCurrentRec({ aiExplain: res.explanation || '' });
    } catch (err) {
      setErrorMsg(err.message);
    } finally {
      setExplaining(false);
    }
  }, [current, explaining, currentUserAnswer, updateCurrentRec, cancelAutoAdvance]);

  const openInlineCorrection = () => {
    if (!current) return;
    cancelAutoAdvance();
    setFixAnswer(submitted?.correctAnswer || current.answer || '');
    setFixExplanation(current.explanation || '');
    setShowCorrection((v) => !v);
  };

  const handleSaveInlineFix = async (e) => {
    e.preventDefault();
    if (!current || !fixAnswer.trim() || savingFix) return;
    setSavingFix(true);
    setErrorMsg('');
    try {
      await aiApi.setCorrection({
        id: current.id,
        pending: false,
        answer: fixAnswer.trim(),
        explanation: fixExplanation.trim(),
      });
      setQueue((prev) =>
        prev.map((q) =>
          q.id === current.id
            ? { ...q, answer: fixAnswer.trim(), explanation: fixExplanation.trim(), isPendingCorrection: false }
            : q
        )
      );
      if (submitted) {
        updateCurrentRec((rec) => ({
          ...rec,
          submitted: {
            ...rec.submitted,
            correctAnswer: fixAnswer.trim(),
            explanation: fixExplanation.trim(),
          },
        }));
      }
      setShowCorrection(false);
      onStatsChanged?.();
      onToast?.('本题参考答案与解析已原地更新生效', 'success');
    } catch (err) {
      setErrorMsg(err.message);
    } finally {
      setSavingFix(false);
    }
  };

  const handleMarkPendingOnly = async () => {
    if (!current || savingFix) return;
    setSavingFix(true);
    try {
      await aiApi.setCorrection({ id: current.id, pending: true });
      setQueue((prev) => prev.map((q) => (q.id === current.id ? { ...q, isPendingCorrection: true } : q)));
      setShowCorrection(false);
      onStatsChanged?.();
      onToast?.('已标记为待修正错题', 'info');
    } catch (err) {
      setErrorMsg(err.message);
    } finally {
      setSavingFix(false);
    }
  };

  // Keyboard shortcuts for fast practice (guarded by isActive!)
  const keyStateRef = useRef({});
  useEffect(() => {
    keyStateRef.current = {
      isActive, showHistory, showCorrection, current, submitted, submitting,
      isJudgement, hasChoices, isMultiple, optionLines, index, queueLength: queue.length,
      handleSubmit, handleNext, handlePrev, handleExplain, currentUserAnswer, updateCurrentRec,
    };
  });

  useEffect(() => {
    const onKeyDown = (e) => {
      const st = keyStateRef.current;
      if (!st.isActive || st.showHistory || st.showCorrection || !st.current) return;
      const tag = e.target?.tagName?.toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select' || e.metaKey || e.ctrlKey || e.altKey) {
        return;
      }

      if (e.key === 'ArrowLeft') {
        if (st.index > 0) {
          e.preventDefault();
          st.handlePrev();
        }
        return;
      }
      if (e.key === 'ArrowRight') {
        if (st.index + 1 < st.queueLength) {
          e.preventDefault();
          st.handleNext();
        }
        return;
      }

      if (e.key === 'Enter') {
        if (st.submitted) {
          e.preventDefault();
          st.handleNext();
        } else if (st.currentUserAnswer()) {
          e.preventDefault();
          void st.handleSubmit();
        }
        return;
      }

      if (e.key.toLowerCase() === 'e') {
        e.preventDefault();
        void st.handleExplain();
        return;
      }

      if (st.submitted) return;

      if (st.isJudgement) {
        if (e.key === '1' || e.key.toLowerCase() === 't') {
          e.preventDefault();
          st.updateCurrentRec({ singleAnswer: '正确' });
        } else if (e.key === '2' || e.key.toLowerCase() === 'f') {
          e.preventDefault();
          st.updateCurrentRec({ singleAnswer: '错误' });
        }
        return;
      }

      if (st.hasChoices && st.optionLines.length > 0) {
        let optIdx = -1;
        if (/^[1-9]$/.test(e.key)) {
          optIdx = Number(e.key) - 1;
        } else if (/^[a-fA-F]$/.test(e.key)) {
          optIdx = e.key.toUpperCase().charCodeAt(0) - 65;
        }
        if (optIdx >= 0 && optIdx < st.optionLines.length) {
          e.preventDefault();
          const stripped = stripOptionPrefix(st.optionLines[optIdx]);
          if (st.isMultiple) {
            st.updateCurrentRec((rec) => ({
              ...rec,
              multiAnswers: rec.multiAnswers.includes(stripped)
                ? rec.multiAnswers.filter((x) => x !== stripped)
                : [...rec.multiAnswers, stripped],
            }));
          } else {
            st.updateCurrentRec({ singleAnswer: stripped });
          }
        }
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  const filteredHistory = useMemo(() => {
    if (historyFilter === 'wrong') return history.items.filter((i) => !i.isCorrect);
    return history.items;
  }, [history.items, historyFilter]);

  const progressPct = queue.length > 0 ? Math.round((sessionScore.answered / queue.length) * 100) : 0;
  const typeMeta = getQuestionTypeMeta(current?.questionType);

  return (
    <div className="space-y-4">
      {errorMsg && (
        <div role="alert" className="flex items-center justify-between rounded-xl border border-danger/25 bg-danger/5 px-4 py-2.5 text-xs text-danger">
          <span>{errorMsg}</span>
          <button type="button" onClick={() => setErrorMsg('')} className="underline">关闭</button>
        </div>
      )}

      {/* 顶部模式与筛选条 */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-line bg-white p-3.5 shadow-card">
        <div className="flex flex-wrap items-center gap-2">
          <div className="inline-flex rounded-xl border border-line bg-canvas p-0.5 text-xs">
            {PRACTICE_MODES.map((m) => (
              <button
                key={m.id}
                type="button"
                onClick={() => setMode(m.id)}
                className={`rounded-lg px-3 py-1.5 font-medium transition ${
                  mode === m.id ? 'bg-brand text-white shadow-xs' : 'text-body hover:text-ink'
                }`}
              >
                {m.label}
              </button>
            ))}
          </div>

          <Select value={folderId} onChange={(e) => setFolderId(e.target.value)} className="h-9 w-40 text-xs">
            <option value="-1">全部文件夹</option>
            {folders.map((f) => <option key={f.id} value={f.id}>{f.name} ({f.questionCount})</option>)}
          </Select>

          <Select value={qType} onChange={(e) => setQType(e.target.value)} className="h-9 w-28 text-xs">
            <option value="all">全部题型</option>
            {QUESTION_TYPE_OPTIONS.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </Select>

          <Button size="sm" variant="outline" onClick={loadQueue} disabled={loading}>
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            换一批
          </Button>
        </div>

        <div className="flex items-center gap-3 text-xs">
          <div className="flex items-center gap-2 rounded-lg bg-canvas px-3 py-1.5 tnum">
            <span className="text-faint">累计 <strong className="text-ink">{history.total}</strong> 题</span>
            <span className="text-line">|</span>
            <span className="text-faint">正确率 <strong className="text-success-ink">{history.accuracy}%</strong></span>
          </div>
          <Button size="sm" variant={showHistory ? 'default' : 'outline'} onClick={() => setShowHistory((v) => !v)}>
            <History className="h-3.5 w-3.5" />
            {showHistory ? '返回刷题' : '练习记录'}
          </Button>
        </div>
      </div>

      {/* 刷题主视图 / 历史记录视图 */}
      {showHistory ? (
        <section className="space-y-3 rounded-2xl border border-line bg-white p-5 shadow-card">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h3 className="text-sm font-semibold text-ink">最近练习记录</h3>
              <p className="text-xs text-faint">共记录 {history.items.length} 次作答</p>
            </div>
            <div className="inline-flex rounded-lg border border-line bg-canvas p-0.5 text-xs">
              <button
                type="button"
                onClick={() => setHistoryFilter('all')}
                className={`rounded-md px-2.5 py-1 font-medium ${historyFilter === 'all' ? 'bg-white text-ink shadow-xs' : 'text-faint'}`}
              >
                全部
              </button>
              <button
                type="button"
                onClick={() => setHistoryFilter('wrong')}
                className={`rounded-md px-2.5 py-1 font-medium ${historyFilter === 'wrong' ? 'bg-white text-danger shadow-xs' : 'text-faint'}`}
              >
                仅看错题
              </button>
            </div>
          </div>

          {filteredHistory.length === 0 ? (
            <p className="py-10 text-center text-xs text-faint">暂无符合条件的练习历史</p>
          ) : (
            <div className="divide-y divide-line rounded-xl border border-line text-xs">
              {filteredHistory.map((item) => (
                <div key={item.id} className="flex items-start justify-between gap-3 px-4 py-3 hover:bg-canvas/50">
                  <div className="min-w-0 flex-1 space-y-1">
                    <p className="font-medium leading-relaxed text-ink">{item.question}</p>
                    <p className="text-faint">
                      你的作答：<span className={item.isCorrect ? 'font-medium text-success-ink' : 'font-medium text-danger'}>{item.userAnswer}</span>
                      <span className="mx-2 text-line">·</span>
                      参考答案：<span className="font-medium text-ink">{item.correctAnswer}</span>
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <button
                      type="button"
                      onClick={() =>
                        onAskTutor?.({
                          contextBadge: '错题复盘',
                          contextTitle: String(item.question || '').slice(0, 36),
                          autoSend: true,
                          prompt: `我在刷题练习中做过这道题，请帮我分析原因并讲透知识点：\n题目：${item.question}\n我的作答：${item.userAnswer}\n参考答案：${item.correctAnswer}`,
                        })
                      }
                      className="rounded-md px-2 py-1 text-[11px] font-medium text-brand hover:bg-brand-soft"
                    >
                      问助教
                    </button>
                    <span className={`rounded-full px-2.5 py-0.5 text-[11px] font-medium ${item.isCorrect ? 'bg-success/10 text-success-ink' : 'bg-danger/10 text-danger'}`}>
                      {item.isCorrect ? '正确' : '错误'}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      ) : loading ? (
        <div className="flex flex-col items-center justify-center gap-2 rounded-2xl border border-line bg-white py-20 text-xs text-faint shadow-card">
          <Loader2 className="h-5 w-5 animate-spin text-brand" />
          <span>正在为你智能编排练习队列...</span>
        </div>
      ) : !current ? (
        <EmptyState
          icon={ClipboardCheck}
          title="当前筛选条件下没有可练习的题目"
          description="请先在「本地题库管理」添加或导入题目，也可以切换其他刷题模式或文件夹。"
          action={(
            <Button size="sm" onClick={() => onNavigate?.('questions')}>
              前往本地题库管理
            </Button>
          )}
        />
      ) : (
        <div className="mx-auto max-w-3xl space-y-4">
          {/* 进度条 + 可点击答题卡 + 自动下一题开关 */}
          <div className="space-y-2.5 rounded-2xl border border-line bg-white px-5 py-3.5 shadow-card">
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
              <div className="flex items-center gap-2">
                <span className="font-semibold text-ink tnum">第 {index + 1} / {queue.length} 题</span>
                <span className="text-line">|</span>
                <span className="font-medium text-success-ink tnum">对 {sessionScore.correct}</span>
                <span className="font-medium text-danger tnum">错 {sessionScore.wrong}</span>
              </div>
              <ToggleSwitch
                id="practice-auto-advance"
                checked={autoAdvance}
                onChange={setAutoAdvance}
                label="答对自动下一题"
              />
            </div>

            <div className="h-1.5 w-full overflow-hidden rounded-full bg-soft">
              <div className="h-full rounded-full bg-brand transition-all duration-300" style={{ width: `${Math.max(3, progressPct)}%` }} />
            </div>

            {queue.length > 1 && (
              <div className="flex flex-wrap gap-1 pt-0.5">
                {queue.map((q, idx) => {
                  const rec = records[idx];
                  const isCurr = idx === index;
                  let cls = 'border-line bg-canvas text-faint hover:border-brand/40 hover:text-ink';
                  if (rec?.submitted) {
                    cls = rec.submitted.isCorrect
                      ? 'border-success/30 bg-success/10 text-success-ink'
                      : 'border-danger/30 bg-danger/10 text-danger';
                  }
                  if (isCurr) {
                    cls = `${cls} ring-2 ring-brand/40 border-brand font-semibold`;
                  }
                  return (
                    <button
                      key={q.id ?? idx}
                      type="button"
                      onClick={() => goToIndex(idx)}
                      title={`跳到第 ${idx + 1} 题`}
                      className={`h-6 min-w-[24px] rounded-md border px-1 text-[11px] tnum transition ${cls}`}
                    >
                      {idx + 1}
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {/* 刷题主卡 */}
          <div className="space-y-5 rounded-2xl border border-line bg-white p-6 shadow-card">
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                <span className={`rounded-md border px-2 py-0.5 text-[11px] font-medium ${typeMeta.cls}`}>
                  {typeMeta.label}
                </span>
                <span className="rounded-md bg-canvas px-2 py-0.5 text-faint">{current.folderName}</span>
                <MasteryDots level={current.mastery} />
                {current.wrongCount > 0 && (
                  <span className="rounded-md bg-warning/15 px-2 py-0.5 font-medium text-warning-ink">
                    曾错 {current.wrongCount} 次
                  </span>
                )}
                {current.isPendingCorrection && (
                  <span className="rounded-md bg-warning/15 px-2 py-0.5 font-medium text-warning-ink">
                    待修正
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                {index > 0 && (
                  <button
                    type="button"
                    onClick={handlePrev}
                    className="inline-flex items-center gap-1 text-xs text-faint hover:text-ink"
                  >
                    <ArrowLeft className="h-3.5 w-3.5" />
                    上一题
                  </button>
                )}
                {!submitted && queue.length > 1 && (
                  <button
                    type="button"
                    onClick={handleNext}
                    className="inline-flex items-center gap-1 text-xs text-faint hover:text-ink"
                  >
                    <SkipForward className="h-3.5 w-3.5" />
                    跳过
                  </button>
                )}
              </div>
            </div>

            <h3 className="text-base font-semibold leading-relaxed text-ink sm:text-[17px]">
              {current.question}
            </h3>

            {/* 答题交互区 */}
            {isJudgement ? (
              <div className="grid max-w-md grid-cols-2 gap-3">
                {['正确', '错误'].map((opt, idx) => {
                  const selected = singleAnswer === opt;
                  const isRight = submitted && isOptionInAnswer(opt, opt, idx, submitted.correctAnswer);
                  const isWrongPick = submitted && selected && !submitted.isCorrect && !isRight;
                  let cls = 'border-line bg-canvas/60 text-ink hover:border-brand/40 hover:bg-brand-soft/30';
                  if (submitted) {
                    if (isRight) cls = 'border-success bg-success/10 text-success-ink font-semibold';
                    else if (isWrongPick) cls = 'border-danger bg-danger/10 text-danger font-semibold';
                    else cls = 'border-line bg-canvas/40 text-faint';
                  } else if (selected) {
                    cls = 'border-brand bg-brand-soft text-brand font-semibold ring-2 ring-brand/15';
                  }
                  return (
                    <button
                      key={opt}
                      type="button"
                      disabled={Boolean(submitted)}
                      onClick={() => updateCurrentRec({ singleAnswer: opt })}
                      className={`flex items-center justify-between rounded-xl border px-4 py-3.5 text-sm transition ${cls}`}
                    >
                      <span className="flex items-center gap-2.5">
                        <span className="inline-flex h-6 w-6 items-center justify-center rounded-md border border-current/20 bg-white/80 font-mono text-xs">
                          {idx + 1}
                        </span>
                        <span>{opt}</span>
                      </span>
                      {submitted && isRight && <CheckCircle2 className="h-4 w-4 text-success" />}
                      {submitted && isWrongPick && <XCircle className="h-4 w-4 text-danger" />}
                    </button>
                  );
                })}
              </div>
            ) : hasChoices ? (
              <div className="space-y-2.5">
                {isMultiple && !submitted && (
                  <p className="text-[11px] text-faint">多选题：可点击多个选项或按键盘 A–F 切换选中</p>
                )}
                {optionLines.map((opt, idx) => {
                  const stripped = stripOptionPrefix(opt);
                  const letter = String.fromCharCode(65 + idx);
                  const active = isMultiple ? multiAnswers.includes(stripped) : singleAnswer === stripped;
                  const isRight = submitted && isOptionInAnswer(opt, stripped, idx, submitted.correctAnswer);
                  const isWrongPick = submitted && active && !isRight;

                  let cardCls = 'border-line bg-canvas/50 text-ink hover:border-brand/40 hover:bg-brand-soft/25';
                  let badgeCls = 'border-line bg-white text-body';
                  if (submitted) {
                    if (isRight) {
                      cardCls = 'border-success bg-success/10 text-success-ink font-medium';
                      badgeCls = 'border-success bg-success text-white';
                    } else if (isWrongPick) {
                      cardCls = 'border-danger bg-danger/10 text-danger font-medium';
                      badgeCls = 'border-danger bg-danger text-white';
                    } else {
                      cardCls = 'border-line/70 bg-canvas/30 text-faint';
                    }
                  } else if (active) {
                    cardCls = 'border-brand bg-brand-soft text-brand font-medium ring-2 ring-brand/15';
                    badgeCls = 'border-brand bg-brand text-white';
                  }

                  return (
                    <button
                      key={idx}
                      type="button"
                      disabled={Boolean(submitted)}
                      onClick={() => {
                        if (isMultiple) {
                          updateCurrentRec((rec) => ({
                            ...rec,
                            multiAnswers: rec.multiAnswers.includes(stripped)
                              ? rec.multiAnswers.filter((x) => x !== stripped)
                              : [...rec.multiAnswers, stripped],
                          }));
                        } else {
                          updateCurrentRec({ singleAnswer: stripped });
                        }
                      }}
                      className={`flex w-full items-center gap-3 rounded-xl border px-4 py-3 text-left text-sm transition ${cardCls}`}
                    >
                      <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md border font-mono text-xs font-semibold ${badgeCls}`}>
                        {letter}
                      </span>
                      <span className="flex-1 leading-relaxed">{stripped}</span>
                      {submitted && isRight && <CheckCircle2 className="h-4 w-4 shrink-0 text-success" />}
                      {submitted && isWrongPick && <XCircle className="h-4 w-4 shrink-0 text-danger" />}
                    </button>
                  );
                })}
              </div>
            ) : (
              <div className="space-y-1.5">
                <Input
                  disabled={Boolean(submitted)}
                  placeholder="请输入你的答案（多空可用 ### 分隔，按 Enter 提交）"
                  value={textAnswer}
                  onChange={(e) => updateCurrentRec({ textAnswer: e.target.value })}
                  onKeyDown={(e) => { if (e.key === 'Enter' && !submitted) void handleSubmit(); }}
                />
              </div>
            )}

            {/* 操作按钮条 */}
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line pt-4">
              <div className="flex flex-wrap items-center gap-2">
                {!submitted ? (
                  <Button size="sm" onClick={handleSubmit} disabled={submitting || !currentUserAnswer()}>
                    {submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
                    {submitting ? '判题中...' : '提交答案'}
                  </Button>
                ) : (
                  <Button size="sm" onClick={handleNext}>
                    {index + 1 < queue.length ? '下一题' : '再来一批'}
                    <ArrowRight className="h-3.5 w-3.5" />
                  </Button>
                )}
                <Button size="sm" variant="outline" onClick={handleExplain} disabled={explaining}>
                  {explaining ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5 text-brand" />}
                  {explaining ? 'AI 解析中...' : 'AI 深度解析'}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    cancelAutoAdvance();
                    onAskTutor?.({
                      contextBadge: '刷题原题辅导',
                      contextTitle: current.question.slice(0, 36),
                      autoSend: true,
                      prompt: `我在刷题时遇到这道题，请帮我讲透背后的知识点与易错点：\n题目：${current.question}${current.options ? `\n选项：\n${current.options}` : ''}${submitted ? `\n我的作答：${currentUserAnswer()}\n参考答案：${submitted.correctAnswer}` : ''}`,
                    });
                  }}
                >
                  <MessageSquare className="h-3.5 w-3.5" />
                  问 AI 助教
                </Button>
              </div>
              <Button size="sm" variant={showCorrection ? 'outline' : 'ghost'} onClick={openInlineCorrection}>
                <Wrench className="h-3.5 w-3.5 text-warning-ink" />
                {showCorrection ? '收起纠错' : '原地修正答案'}
              </Button>
            </div>

            {/* 原地纠错编辑器：无需离开刷题页！ */}
            {showCorrection && (
              <form
                onSubmit={handleSaveInlineFix}
                className="space-y-3 rounded-xl border border-warning/35 bg-warning/5 p-4 text-xs animate-fade-in"
              >
                <div className="flex items-center justify-between">
                  <p className="font-semibold text-warning-ink">原地修正本题参考答案（保存后立即生效，无需跳转题库）</p>
                </div>
                <div className="space-y-1">
                  <Label>正确参考答案（多选用 ### 分隔）</Label>
                  <Input
                    value={fixAnswer}
                    onChange={(e) => setFixAnswer(e.target.value)}
                    className="h-8 bg-white text-xs"
                    placeholder="输入修正后的标准答案..."
                    required
                  />
                </div>
                <div className="space-y-1">
                  <Label>题目解析（选填）</Label>
                  <textarea
                    rows={2}
                    value={fixExplanation}
                    onChange={(e) => setFixExplanation(e.target.value)}
                    placeholder="补充正确的解题思路..."
                    className="w-full rounded-lg border border-line bg-white px-2.5 py-1.5 text-xs text-ink focus:border-brand focus:outline-none"
                  />
                </div>
                <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={savingFix || current.isPendingCorrection}
                    onClick={() => void handleMarkPendingOnly()}
                  >
                    <AlertTriangle className="h-3.5 w-3.5 text-warning-ink" />
                    {current.isPendingCorrection ? '已标为待修正' : '仅标为待修正（稍后改）'}
                  </Button>
                  <div className="flex gap-1.5">
                    <Button type="button" size="sm" variant="outline" onClick={() => setShowCorrection(false)}>
                      取消
                    </Button>
                    <Button type="submit" size="sm" disabled={savingFix || !fixAnswer.trim()}>
                      {savingFix ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                      保存修正
                    </Button>
                  </div>
                </div>
              </form>
            )}

            {/* 判题结果 */}
            {submitted && (
              <div
                className={`space-y-2.5 rounded-xl border p-4 text-sm animate-fade-in ${
                  submitted.isCorrect
                    ? 'border-success/30 bg-success/5 text-success-ink'
                    : 'border-danger/30 bg-danger/5 text-danger'
                }`}
              >
                <div className="flex items-center gap-2 font-semibold">
                  {submitted.isCorrect ? <CheckCircle2 className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
                  <span>{submitted.isCorrect ? '回答正确！掌握度已提升并同步遗忘曲线' : '回答有误，已自动加入错题强化'}</span>
                </div>
                <p className="text-xs text-ink">
                  参考答案：<strong className="font-semibold">{submitted.correctAnswer}</strong>
                </p>
                {(aiExplain || submitted.explanation || current.explanation) && (
                  <div className="rounded-xl border border-line bg-white p-3.5">
                    <p className="mb-1.5 text-xs font-semibold text-ink">题目解析</p>
                    <MarkdownContent content={aiExplain || submitted.explanation || current.explanation} />
                  </div>
                )}
              </div>
            )}

            {!submitted && aiExplain && (
              <div className="rounded-xl border border-brand/20 bg-brand-soft/40 p-4 animate-fade-in">
                <p className="mb-1.5 text-xs font-semibold text-brand">AI 思路解析</p>
                <MarkdownContent content={aiExplain} />
              </div>
            )}

            {/* 快捷键提示 */}
            <div className="flex items-center gap-1.5 pt-1 text-[11px] text-faint">
              <Keyboard className="h-3.5 w-3.5 shrink-0" />
              <span>快捷键：{isJudgement ? '1 正确 · 2 错误' : hasChoices ? 'A–F / 1–6 选答案' : '输入后'} · Enter 提交 / 下一题 · ←/→ 切换题目 · E 呼叫 AI 解析</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
