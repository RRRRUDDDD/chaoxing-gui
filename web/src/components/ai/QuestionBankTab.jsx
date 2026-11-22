import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import Button from '../ui/Button';
import Input from '../ui/Input';
import Label from '../ui/Label';
import Select from '../ui/Select';
import { aiApi } from '../../api/aiApi';
import {
  QUESTION_TYPE_OPTIONS, getQuestionTypeMeta, MasteryDots, ToggleSwitch,
  CopyButton, ModalDialog, ConfirmDialog, EmptyState,
} from './aiShared';
import {
  Folder, FolderOpen, FolderPlus, Plus, Search, Trash2, Upload, Download,
  Edit3, AlertTriangle, CheckCircle2, FolderInput, X, Loader2, FileUp,
  MessageSquare, Database, Check, ClipboardCheck, BrainCircuit, Wrench,
} from 'lucide-react';

const emptyForm = (folderId = 0) => ({
  id: null,
  question: '',
  options: '',
  questionType: 'single',
  answer: '',
  explanation: '',
  folderId,
  isAi: false,
  isPendingCorrection: false,
  difficulty: 0,
  importance: 0,
  mastery: 0,
});

const SAMPLE_JSON = JSON.stringify(
  [
    {
      question: '以下哪个协议属于传输层协议？',
      options: 'A. HTTP\nB. TCP\nC. IP\nD. ARP',
      questionType: 'single',
      answer: 'TCP',
      explanation: 'TCP 和 UDP 位于 OSI/TCP-IP 模型的传输层。',
    },
  ],
  null,
  2
);

const SAMPLE_CSV = 'question,options,questionType,answer\n"以下哪个协议属于传输层协议？","A. HTTP\nB. TCP\nC. IP\nD. ARP",single,TCP';

export default function QuestionBankTab({
  isActive = true,
  intent,
  pendingCount = 0,
  dataVersion = 0,
  onStatsChanged,
  onNavigate,
  onAskTutor,
  onToast,
}) {
  const [folders, setFolders] = useState([]);
  const [activeFolderId, setActiveFolderId] = useState(() => (intent?.folderId !== undefined ? intent.folderId : -1));
  const [questions, setQuestions] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [keywordInput, setKeywordInput] = useState(() => intent?.keyword || '');
  const [keyword, setKeyword] = useState(() => intent?.keyword || '');
  const [typeFilter, setTypeFilter] = useState('all');
  const [sourceFilter, setSourceFilter] = useState('all');
  const [loading, setLoading] = useState(false);
  const [selectedIds, setSelectedIds] = useState([]);
  const [errorMsg, setErrorMsg] = useState('');

  // Inline quick-fix state for pending corrections
  const [inlineFixId, setInlineFixId] = useState(null);
  const [inlineFixAnswer, setInlineFixAnswer] = useState('');
  const [inlineFixSaving, setInlineFixSaving] = useState(false);

  // Folder editing
  const [newFolderName, setNewFolderName] = useState('');
  const [showNewFolder, setShowNewFolder] = useState(false);
  const [renamingFolderId, setRenamingFolderId] = useState(null);
  const [renameText, setRenameText] = useState('');
  const [folderToDelete, setFolderToDelete] = useState(null);
  const [deleteFolderWithQs, setDeleteFolderWithQs] = useState(false);

  // Modals & confirmations
  const [editing, setEditing] = useState(null);
  const [saving, setSaving] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [importFormat, setImportFormat] = useState('json');
  const [importContent, setImportContent] = useState('');
  const [importing, setImporting] = useState(false);
  const [moveTargetFolder, setMoveTargetFolder] = useState('0');
  const [idsToDelete, setIdsToDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const fileInputRef = useRef(null);
  const [prevIntentTs, setPrevIntentTs] = useState(intent?._ts || null);
  const syncedVersionRef = useRef(dataVersion);

  // React to navigation intents cleanly without remounting
  if (intent?._ts && intent._ts !== prevIntentTs) {
    setPrevIntentTs(intent._ts);
    if (intent.folderId !== undefined) {
      setActiveFolderId(intent.folderId);
      setPage(1);
    }
    if (intent.keyword !== undefined) {
      setKeywordInput(intent.keyword);
      setKeyword(intent.keyword);
      setPage(1);
    }
  }

  // Debounce search input
  useEffect(() => {
    const t = setTimeout(() => {
      setKeyword((prev) => {
        if (prev !== keywordInput) setPage(1);
        return keywordInput;
      });
    }, 240);
    return () => clearTimeout(t);
  }, [keywordInput]);

  const loadFolders = useCallback(async () => {
    const data = await aiApi.listFolders();
    setFolders(data.items || []);
  }, []);

  const loadQuestions = useCallback(async () => {
    setLoading(true);
    setErrorMsg('');
    try {
      const data = await aiApi.listQuestions({
        folderId: activeFolderId >= 0 ? activeFolderId : null,
        pendingOnly: activeFolderId === -2,
        keyword,
        questionType: typeFilter,
        source: sourceFilter,
        page,
        pageSize: 30,
      });
      setQuestions(data.items || []);
      setTotal(data.total || 0);
      setSelectedIds([]);
    } catch (err) {
      setErrorMsg(err.message);
    } finally {
      setLoading(false);
    }
  }, [activeFolderId, keyword, typeFilter, sourceFilter, page]);

  useEffect(() => {
    let cancelled = false;
    aiApi.listFolders()
      .then((data) => { if (!cancelled) setFolders(data.items || []); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    aiApi.listQuestions({
      folderId: activeFolderId >= 0 ? activeFolderId : null,
      pendingOnly: activeFolderId === -2,
      keyword,
      questionType: typeFilter,
      source: sourceFilter,
      page,
      pageSize: 30,
    })
      .then((data) => {
        if (cancelled) return;
        setQuestions(data.items || []);
        setTotal(data.total || 0);
        setSelectedIds([]);
        setErrorMsg('');
      })
      .catch((err) => { if (!cancelled) setErrorMsg(err.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [activeFolderId, keyword, typeFilter, sourceFilter, page]);

  // Soft-sync when returning to this tab after other tabs mutated questions/folders
  useEffect(() => {
    if (isActive && dataVersion !== syncedVersionRef.current) {
      syncedVersionRef.current = dataVersion;
      void Promise.all([loadFolders(), loadQuestions()]);
    }
  }, [isActive, dataVersion, loadFolders, loadQuestions]);

  const totalFolderQuestions = useMemo(
    () => folders.reduce((sum, f) => sum + (Number(f.questionCount) || 0), 0),
    [folders]
  );

  const folderTree = useMemo(() => {
    const map = new Map();
    folders.forEach((f) => map.set(f.id, { ...f, children: [] }));
    const roots = [];
    map.forEach((node) => {
      if (node.id !== 0 && node.parentId && map.has(node.parentId)) {
        map.get(node.parentId).children.push(node);
      } else {
        roots.push(node);
      }
    });
    return roots;
  }, [folders]);

  const activeFolderLabel = useMemo(() => {
    if (activeFolderId === -1) return '全部题目';
    if (activeFolderId === -2) return '待修正错题';
    return folders.find((f) => f.id === activeFolderId)?.name || '默认文件夹';
  }, [activeFolderId, folders]);

  const allSelectedOnPage = questions.length > 0 && selectedIds.length === questions.length;

  const toggleSelectAll = () => {
    if (allSelectedOnPage) setSelectedIds([]);
    else setSelectedIds(questions.map((q) => q.id));
  };

  const handleCreateFolder = async (e) => {
    e.preventDefault();
    if (!newFolderName.trim()) return;
    try {
      const parentId = activeFolderId > 0 ? activeFolderId : 0;
      await aiApi.createFolder(newFolderName.trim(), parentId);
      setNewFolderName('');
      setShowNewFolder(false);
      await loadFolders();
      onStatsChanged?.();
      onToast?.('文件夹已创建', 'success');
    } catch (err) {
      setErrorMsg(err.message);
    }
  };

  const handleRenameFolder = async (e) => {
    e.preventDefault();
    if (renamingFolderId == null || !renameText.trim()) return;
    try {
      await aiApi.renameFolder(renamingFolderId, renameText.trim());
      setRenamingFolderId(null);
      setRenameText('');
      await Promise.all([loadFolders(), loadQuestions()]);
      onToast?.('文件夹已重命名', 'success');
    } catch (err) {
      setErrorMsg(err.message);
    }
  };

  const handleConfirmDeleteFolder = async () => {
    if (!folderToDelete || folderToDelete.id === 0) return;
    setDeleting(true);
    try {
      await aiApi.deleteFolder(folderToDelete.id, deleteFolderWithQs);
      if (activeFolderId === folderToDelete.id) setActiveFolderId(-1);
      setFolderToDelete(null);
      setDeleteFolderWithQs(false);
      await Promise.all([loadFolders(), loadQuestions()]);
      onStatsChanged?.();
      onToast?.('文件夹已删除', 'success');
    } catch (err) {
      setErrorMsg(err.message);
    } finally {
      setDeleting(false);
    }
  };

  const saveQuestionPayload = async (payload) => {
    if (!payload || saving) return;
    setSaving(true);
    setErrorMsg('');
    try {
      await aiApi.saveQuestion(payload);
      const isEdit = Boolean(payload.id);
      setEditing(null);
      await Promise.all([loadFolders(), loadQuestions()]);
      onStatsChanged?.();
      onToast?.(isEdit ? '题目修改已保存' : '已新增题目到题库', 'success');
    } catch (err) {
      setErrorMsg(err.message);
    } finally {
      setSaving(false);
    }
  };

  const handleSaveQuestion = async (e) => {
    e.preventDefault();
    await saveQuestionPayload(editing);
  };

  const handleInlineFixSubmit = async (e, q) => {
    e.preventDefault();
    if (!inlineFixAnswer.trim() || inlineFixSaving) return;
    setInlineFixSaving(true);
    setErrorMsg('');
    try {
      await aiApi.setCorrection({
        id: q.id,
        pending: false,
        answer: inlineFixAnswer.trim(),
      });
      setInlineFixId(null);
      setInlineFixAnswer('');
      await loadQuestions();
      onStatsChanged?.();
      onToast?.('答案已修正并解除待修正状态', 'success');
    } catch (err) {
      setErrorMsg(err.message);
    } finally {
      setInlineFixSaving(false);
    }
  };

  const handleConfirmDeleteQuestions = async () => {
    if (!idsToDelete?.length) return;
    setDeleting(true);
    try {
      await aiApi.deleteQuestions(idsToDelete);
      const count = idsToDelete.length;
      setIdsToDelete(null);
      await Promise.all([loadFolders(), loadQuestions()]);
      onStatsChanged?.();
      onToast?.(`已删除 ${count} 道题目`, 'success');
    } catch (err) {
      setErrorMsg(err.message);
    } finally {
      setDeleting(false);
    }
  };

  const handleBatchMove = async () => {
    if (!selectedIds.length) return;
    try {
      const count = selectedIds.length;
      await aiApi.moveQuestions(selectedIds, Number(moveTargetFolder));
      await Promise.all([loadFolders(), loadQuestions()]);
      onToast?.(`已移动 ${count} 道题目`, 'success');
    } catch (err) {
      setErrorMsg(err.message);
    }
  };

  const handleTogglePending = async (q) => {
    try {
      const next = !q.isPendingCorrection;
      await aiApi.setCorrection({ id: q.id, pending: next });
      if (!next && inlineFixId === q.id) setInlineFixId(null);
      await loadQuestions();
      onStatsChanged?.();
      onToast?.(next ? '已标记为待修正错题' : '已取消待修正标记', 'info');
    } catch (err) {
      setErrorMsg(err.message);
    }
  };

  const handleImportFile = (file) => {
    if (!file) return;
    const lower = file.name.toLowerCase();
    if (lower.endsWith('.csv')) setImportFormat('csv');
    else if (lower.endsWith('.json')) setImportFormat('json');
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result === 'string') {
        setImportContent(reader.result);
      }
    };
    reader.readAsText(file, 'utf-8');
  };

  const handleImport = async (e) => {
    e.preventDefault();
    if (!importContent.trim() || importing) return;
    setImporting(true);
    setErrorMsg('');
    try {
      const res = await aiApi.importQuestions({
        format: importFormat,
        content: importContent,
        folderId: activeFolderId >= 0 ? activeFolderId : 0,
      });
      setShowImport(false);
      setImportContent('');
      await Promise.all([loadFolders(), loadQuestions()]);
      onStatsChanged?.();
      onToast?.(`成功导入 ${res.imported} 题（跳过重复 ${res.skipped} 题）`, 'success');
    } catch (err) {
      setErrorMsg(err.message);
    } finally {
      setImporting(false);
    }
  };

  const handleExport = async (fmt) => {
    try {
      const res = await aiApi.exportQuestions({
        format: fmt,
        folderId: activeFolderId >= 0 ? activeFolderId : null,
      });
      const blob = new Blob([res.content], {
        type: fmt === 'csv' ? 'text/csv;charset=utf-8' : 'application/json;charset=utf-8',
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `ai-questions.${fmt}`;
      a.click();
      URL.revokeObjectURL(url);
      onToast?.(`已导出 ${res.count} 道题目 (${fmt.toUpperCase()})`, 'success');
    } catch (err) {
      setErrorMsg(err.message);
    }
  };

  const renderFolderNode = (node, depth = 0) => {
    const active = activeFolderId === node.id;
    const isRenaming = renamingFolderId === node.id;
    return (
      <div key={node.id}>
        {isRenaming ? (
          <form onSubmit={handleRenameFolder} className="flex items-center gap-1 py-1" style={{ paddingLeft: `${8 + depth * 12}px` }}>
            <Input
              value={renameText}
              onChange={(e) => setRenameText(e.target.value)}
              className="h-7 text-xs"
              autoFocus
            />
            <button type="submit" className="rounded p-1 text-brand hover:bg-brand-soft" title="确认重命名">
              <Check className="h-3.5 w-3.5" />
            </button>
            <button type="button" onClick={() => setRenamingFolderId(null)} className="rounded p-1 text-faint hover:bg-soft" title="取消">
              <X className="h-3.5 w-3.5" />
            </button>
          </form>
        ) : (
          <div
            className={`group flex items-center justify-between rounded-lg px-2.5 py-2 text-xs transition ${
              active ? 'bg-brand-soft font-medium text-brand' : 'text-body hover:bg-soft hover:text-ink'
            }`}
            style={{ paddingLeft: `${10 + depth * 14}px` }}
          >
            <button
              type="button"
              onClick={() => { setActiveFolderId(node.id); setPage(1); }}
              className="flex min-w-0 flex-1 items-center gap-2 text-left"
            >
              {active ? <FolderOpen className="h-3.5 w-3.5 shrink-0" /> : <Folder className="h-3.5 w-3.5 shrink-0 text-faint" />}
              <span className="truncate">{node.name}</span>
            </button>
            <div className="flex items-center gap-1">
              <span className="text-[11px] text-faint tnum">{node.questionCount}</span>
              {node.id !== 0 && (
                <>
                  <button
                    type="button"
                    onClick={() => { setRenamingFolderId(node.id); setRenameText(node.name); }}
                    title="重命名文件夹"
                    className="hidden rounded p-0.5 text-faint hover:text-brand group-hover:inline-flex"
                  >
                    <Edit3 className="h-3 w-3" />
                  </button>
                  <button
                    type="button"
                    onClick={() => { setFolderToDelete(node); setDeleteFolderWithQs(false); }}
                    title="删除文件夹"
                    className="hidden rounded p-0.5 text-faint hover:text-danger group-hover:inline-flex"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                </>
              )}
            </div>
          </div>
        )}
        {node.children.map((child) => renderFolderNode(child, depth + 1))}
      </div>
    );
  };

  return (
    <div className="space-y-4">
      {errorMsg && (
        <div role="alert" className="flex items-center justify-between rounded-xl border border-danger/25 bg-danger/5 px-4 py-2.5 text-xs text-danger">
          <span>{errorMsg}</span>
          <button type="button" onClick={() => setErrorMsg('')} className="underline">关闭</button>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-12">
        {/* 左侧文件夹树 */}
        <aside className="h-fit space-y-2.5 rounded-2xl border border-line bg-white p-3.5 shadow-card lg:sticky lg:top-0 lg:col-span-3">
          <div className="flex items-center justify-between px-1">
            <h3 className="text-xs font-semibold text-ink">题库目录</h3>
            <button
              type="button"
              onClick={() => setShowNewFolder((v) => !v)}
              className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-brand hover:bg-brand-soft"
            >
              <FolderPlus className="h-3.5 w-3.5" />
              新建
            </button>
          </div>

          {showNewFolder && (
            <form onSubmit={handleCreateFolder} className="flex gap-1.5 rounded-xl border border-line bg-canvas p-2">
              <Input
                value={newFolderName}
                onChange={(e) => setNewFolderName(e.target.value)}
                placeholder="输入文件夹名..."
                className="h-8 text-xs"
                autoFocus
              />
              <Button type="submit" size="sm">添加</Button>
            </form>
          )}

          <div className="space-y-0.5">
            <button
              type="button"
              onClick={() => { setActiveFolderId(-1); setPage(1); }}
              className={`flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-xs transition ${
                activeFolderId === -1 ? 'bg-brand-soft font-medium text-brand' : 'text-body hover:bg-soft hover:text-ink'
              }`}
            >
              <span className="flex items-center gap-2">
                <Database className="h-3.5 w-3.5" />
                全部题目
              </span>
              <span className="text-[11px] text-faint tnum">{totalFolderQuestions}</span>
            </button>
            <button
              type="button"
              onClick={() => { setActiveFolderId(-2); setPage(1); }}
              className={`flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-xs transition ${
                activeFolderId === -2 ? 'bg-warning/15 font-medium text-warning-ink' : 'text-body hover:bg-soft hover:text-ink'
              }`}
            >
              <span className="flex items-center gap-2">
                <AlertTriangle className="h-3.5 w-3.5 text-warning-ink" />
                待修正错题
              </span>
              {pendingCount > 0 && (
                <span className="rounded-full bg-warning/20 px-1.5 py-0.5 text-[10px] font-semibold text-warning-ink tnum">
                  {pendingCount}
                </span>
              )}
            </button>

            <div className="my-2 border-t border-line" />
            <div className="max-h-[420px] space-y-0.5 overflow-y-auto pr-0.5">
              {folderTree.map((root) => renderFolderNode(root))}
            </div>
          </div>
        </aside>

        {/* 右侧题目列表 */}
        <div className="space-y-3.5 rounded-2xl border border-line bg-white p-4 shadow-card lg:col-span-9">
          {/* 筛选与操作栏 */}
          <div className="flex flex-wrap items-center justify-between gap-2.5">
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
              <div className="relative min-w-[200px] flex-1">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-faint" />
                <Input
                  value={keywordInput}
                  onChange={(e) => setKeywordInput(e.target.value)}
                  placeholder="搜索题干、选项或答案..."
                  className="h-9 pl-8 pr-8 text-xs"
                />
                {keywordInput && (
                  <button
                    type="button"
                    onClick={() => { setKeywordInput(''); setKeyword(''); setPage(1); }}
                    aria-label="清除搜索"
                    className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-faint hover:bg-soft hover:text-ink"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
              <Select
                value={typeFilter}
                onChange={(e) => { setTypeFilter(e.target.value); setPage(1); }}
                className="h-9 w-28 text-xs"
              >
                <option value="all">全部题型</option>
                {QUESTION_TYPE_OPTIONS.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
              </Select>
              <Select
                value={sourceFilter}
                onChange={(e) => { setSourceFilter(e.target.value); setPage(1); }}
                className="h-9 w-28 text-xs"
              >
                <option value="all">全部来源</option>
                <option value="ai">AI 生成</option>
                <option value="manual">手动 / OCS</option>
              </Select>
            </div>

            <div className="flex flex-wrap items-center gap-1.5">
              <Button size="sm" onClick={() => setEditing(emptyForm(activeFolderId >= 0 ? activeFolderId : 0))}>
                <Plus className="h-3.5 w-3.5" />
                新增题目
              </Button>
              <Button size="sm" variant="outline" onClick={() => setShowImport(true)}>
                <Upload className="h-3.5 w-3.5" />
                导入
              </Button>
              <Button size="sm" variant="outline" onClick={() => void handleExport('json')}>
                <Download className="h-3.5 w-3.5" />
                导出 JSON
              </Button>
              <Button size="sm" variant="ghost" onClick={() => void handleExport('csv')}>
                CSV
              </Button>
            </div>
          </div>

          {/* 列表表头 / 批量操作栏 + 直达刷题与生成图谱 */}
          {selectedIds.length > 0 ? (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-brand/25 bg-brand-soft/60 px-3.5 py-2 text-xs">
              <div className="flex items-center gap-2.5">
                <label className="flex cursor-pointer items-center gap-1.5 font-medium text-brand">
                  <input
                    type="checkbox"
                    className="h-3.5 w-3.5 accent-brand"
                    checked={allSelectedOnPage}
                    onChange={toggleSelectAll}
                  />
                  已选 {selectedIds.length} 题
                </label>
                <button type="button" onClick={() => setSelectedIds([])} className="text-faint hover:text-ink hover:underline">
                  取消选择
                </button>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Select value={moveTargetFolder} onChange={(e) => setMoveTargetFolder(e.target.value)} className="h-8 w-36 text-xs">
                  {folders.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
                </Select>
                <Button size="sm" variant="outline" onClick={handleBatchMove}>
                  <FolderInput className="h-3.5 w-3.5" />
                  移动到
                </Button>
                <Button size="sm" variant="destructive" onClick={() => setIdsToDelete(selectedIds)}>
                  <Trash2 className="h-3.5 w-3.5" />
                  批量删除
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-canvas/80 px-3.5 py-2 text-xs text-faint">
              <div className="flex items-center gap-2.5">
                <label className="flex cursor-pointer items-center gap-2 font-medium text-body">
                  <input
                    type="checkbox"
                    className="h-3.5 w-3.5 accent-brand"
                    disabled={!questions.length}
                    checked={allSelectedOnPage}
                    onChange={toggleSelectAll}
                  />
                  <span>{activeFolderLabel}</span>
                </label>
                <span className="tnum">共 {total} 道题目</span>
              </div>

              {total > 0 && (
                <div className="flex flex-wrap items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() =>
                      onNavigate?.('practice', {
                        folderId: activeFolderId >= 0 ? activeFolderId : -1,
                        mode: activeFolderId === -2 ? 'wrong' : 'smart',
                      })
                    }
                    className="inline-flex items-center gap-1 rounded-lg border border-brand/25 bg-white px-2.5 py-1 text-[11px] font-medium text-brand transition hover:bg-brand-soft"
                  >
                    <ClipboardCheck className="h-3.5 w-3.5" />
                    {activeFolderId === -2 ? '专练错题' : '刷当前目录'}
                  </button>
                  {activeFolderId >= 0 && (
                    <button
                      type="button"
                      onClick={() =>
                        onNavigate?.('study', {
                          openGenerate: true,
                          folderId: activeFolderId,
                          folderName: activeFolderLabel,
                        })
                      }
                      className="inline-flex items-center gap-1 rounded-lg border border-line bg-white px-2.5 py-1 text-[11px] font-medium text-body transition hover:border-brand/30 hover:text-brand"
                    >
                      <BrainCircuit className="h-3.5 w-3.5" />
                      生成图谱
                    </button>
                  )}
                </div>
              )}
            </div>
          )}

          {/* 题目卡片流 */}
          {loading ? (
            <div className="flex items-center justify-center py-16 text-xs text-faint">
              <Loader2 className="mr-2 h-4 w-4 animate-spin text-brand" />
              加载题库中...
            </div>
          ) : questions.length === 0 ? (
            <EmptyState
              icon={Database}
              title="当前条件下暂无题目"
              description="自动刷课查到的新题会自动沉淀到这里；你也可以手动新增，或通过 JSON / CSV 批量导入现有题库。"
              action={(
                <div className="flex items-center gap-2">
                  <Button size="sm" onClick={() => setEditing(emptyForm(activeFolderId >= 0 ? activeFolderId : 0))}>
                    <Plus className="h-3.5 w-3.5" />
                    新增题目
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => setShowImport(true)}>
                    <Upload className="h-3.5 w-3.5" />
                    导入题库
                  </Button>
                </div>
              )}
            />
          ) : (
            <div className="space-y-2.5">
              {questions.map((q) => {
                const checked = selectedIds.includes(q.id);
                const typeMeta = getQuestionTypeMeta(q.questionType);
                const optionLines = String(q.options || '').split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
                const isInlineFixing = inlineFixId === q.id;
                return (
                  <article
                    key={q.id}
                    className={`rounded-xl border p-4 transition ${
                      checked
                        ? 'border-brand/40 bg-brand-soft/20'
                        : q.isPendingCorrection
                          ? 'border-warning/40 bg-white hover:shadow-card'
                          : 'border-line bg-white hover:border-faint/40 hover:shadow-card'
                    }`}
                  >
                    <div className="flex items-start gap-3">
                      <input
                        type="checkbox"
                        className="mt-1 h-4 w-4 shrink-0 accent-brand"
                        checked={checked}
                        onChange={() =>
                          setSelectedIds((prev) => (checked ? prev.filter((id) => id !== q.id) : [...prev, q.id]))
                        }
                      />

                      <div className="min-w-0 flex-1 space-y-2">
                        {/* 元信息条 */}
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <span className={`rounded-md border px-2 py-0.5 text-[11px] font-medium ${typeMeta.cls}`}>
                              {typeMeta.label}
                            </span>
                            <span className="rounded-md bg-canvas px-2 py-0.5 text-[11px] text-faint">
                              {q.folderName}
                            </span>
                            {q.isAi && (
                              <span className="rounded-md bg-brand-soft px-1.5 py-0.5 text-[10px] font-medium text-brand">
                                AI
                              </span>
                            )}
                            {q.isPendingCorrection && (
                              <span className="rounded-md bg-warning/15 px-2 py-0.5 text-[11px] font-medium text-warning-ink">
                                待修正
                              </span>
                            )}
                            <MasteryDots level={q.mastery} />
                          </div>

                          <div className="flex items-center gap-0.5">
                            <CopyButton text={q.answer} label="复制答案" />
                            <button
                              type="button"
                              onClick={() =>
                                onAskTutor?.({
                                  contextBadge: '题库原题讲解',
                                  contextTitle: q.question.slice(0, 40),
                                  autoSend: true,
                                  prompt: `请帮我详细讲解这道题并指出易错点：\n题目：${q.question}${q.options ? `\n选项：\n${q.options}` : ''}\n当前答案：${q.answer}`,
                                })
                              }
                              title="用快捷 AI 助教讲解此题"
                              className="rounded-md p-1.5 text-faint transition hover:bg-soft hover:text-brand"
                            >
                              <MessageSquare className="h-3.5 w-3.5" />
                            </button>
                            <button
                              type="button"
                              onClick={() => void handleTogglePending(q)}
                              title={q.isPendingCorrection ? '取消待修正标记' : '标记为错题/待修正'}
                              className={`rounded-md p-1.5 transition hover:bg-soft ${
                                q.isPendingCorrection ? 'text-warning-ink' : 'text-faint hover:text-warning-ink'
                              }`}
                            >
                              {q.isPendingCorrection ? <CheckCircle2 className="h-3.5 w-3.5" /> : <AlertTriangle className="h-3.5 w-3.5" />}
                            </button>
                            <button
                              type="button"
                              onClick={() => setEditing({ ...q })}
                              title="编辑题目"
                              className="rounded-md p-1.5 text-faint transition hover:bg-soft hover:text-brand"
                            >
                              <Edit3 className="h-3.5 w-3.5" />
                            </button>
                            <button
                              type="button"
                              onClick={() => setIdsToDelete([q.id])}
                              title="删除题目"
                              className="rounded-md p-1.5 text-faint transition hover:bg-soft hover:text-danger"
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          </div>
                        </div>

                        {/* 题干 */}
                        <p className="text-[13px] font-medium leading-relaxed text-ink">{q.question}</p>

                        {/* 选项 */}
                        {optionLines.length > 0 && (
                          <div className="grid gap-1 rounded-lg bg-canvas/70 px-3 py-2 text-xs text-body sm:grid-cols-2">
                            {optionLines.map((ln, idx) => (
                              <p key={idx} className="leading-relaxed">{ln}</p>
                            ))}
                          </div>
                        )}

                        {/* 答案 + 待修正原地速改 */}
                        {isInlineFixing ? (
                          <form
                            onSubmit={(e) => void handleInlineFixSubmit(e, q)}
                            className="flex flex-wrap items-center gap-2 rounded-lg border border-warning/40 bg-warning/5 p-2.5"
                          >
                            <Input
                              value={inlineFixAnswer}
                              onChange={(e) => setInlineFixAnswer(e.target.value)}
                              placeholder="输入修正后的标准答案（多选用 ### 分隔）"
                              className="h-8 min-w-[200px] flex-1 bg-white text-xs"
                              autoFocus
                              required
                            />
                            <Button type="submit" size="sm" disabled={inlineFixSaving || !inlineFixAnswer.trim()}>
                              {inlineFixSaving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                              保存并解除待修正
                            </Button>
                            <Button type="button" size="sm" variant="outline" onClick={() => setInlineFixId(null)}>
                              取消
                            </Button>
                          </form>
                        ) : (
                          <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-success/20 bg-success/5 px-3 py-1.5 text-xs">
                            <div className="flex min-w-0 flex-wrap items-baseline gap-2">
                              <span className="shrink-0 font-semibold text-success-ink">参考答案</span>
                              <span className="font-medium text-ink">{q.answer}</span>
                            </div>
                            {q.isPendingCorrection && (
                              <button
                                type="button"
                                onClick={() => {
                                  setInlineFixId(q.id);
                                  setInlineFixAnswer(q.answer || '');
                                }}
                                className="inline-flex shrink-0 items-center gap-1 rounded-md bg-warning/15 px-2 py-0.5 text-[11px] font-medium text-warning-ink transition hover:bg-warning/25"
                              >
                                <Wrench className="h-3 w-3" />
                                原地速改答案
                              </button>
                            )}
                          </div>
                        )}

                        {q.explanation && (
                          <details className="text-xs text-faint">
                            <summary className="cursor-pointer font-medium text-body hover:text-brand">查看题目解析</summary>
                            <p className="mt-1.5 whitespace-pre-wrap rounded-lg bg-canvas px-3 py-2 leading-relaxed text-body">
                              {q.explanation}
                            </p>
                          </details>
                        )}
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          )}

          {/* 分页 */}
          {total > 30 && (
            <div className="flex items-center justify-between border-t border-line pt-3 text-xs text-faint">
              <span className="tnum">共 {total} 题 · 第 {page} / {Math.ceil(total / 30)} 页</span>
              <div className="flex gap-2">
                <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>上一页</Button>
                <Button size="sm" variant="outline" disabled={page * 30 >= total} onClick={() => setPage((p) => p + 1)}>下一页</Button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 新增/编辑题目弹窗 */}
      <ModalDialog
        open={Boolean(editing)}
        onClose={() => setEditing(null)}
        title={editing?.id ? '编辑题目' : '新增题目'}
        subtitle="多选题或多个填空答案请使用 ### 分隔"
      >
        {editing && (
          <form onSubmit={handleSaveQuestion} className="space-y-3.5">
            <div className="grid gap-3 sm:grid-cols-3">
              <div className="space-y-1">
                <Label>所属文件夹</Label>
                <Select
                  value={editing.folderId}
                  onChange={(e) => setEditing({ ...editing, folderId: Number(e.target.value) })}
                  className="h-9 text-xs"
                >
                  {folders.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
                </Select>
              </div>
              <div className="space-y-1">
                <Label>题型</Label>
                <Select
                  value={editing.questionType}
                  onChange={(e) => setEditing({ ...editing, questionType: e.target.value })}
                  className="h-9 text-xs"
                >
                  {QUESTION_TYPE_OPTIONS.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                </Select>
              </div>
              <div className="space-y-1">
                <Label>掌握程度</Label>
                <Select
                  value={String(editing.mastery ?? 0)}
                  onChange={(e) => setEditing({ ...editing, mastery: Number(e.target.value) })}
                  className="h-9 text-xs"
                >
                  <option value="0">0 · 未学习</option>
                  <option value="1">1 · 生疏</option>
                  <option value="2">2 · 熟悉</option>
                  <option value="3">3 · 已掌握</option>
                </Select>
              </div>
            </div>

            <div className="space-y-1">
              <Label>题干</Label>
              <textarea
                rows={3}
                required
                placeholder="输入题目题干..."
                value={editing.question}
                onChange={(e) => setEditing({ ...editing, question: e.target.value })}
                className="w-full rounded-lg border border-line px-3 py-2 text-sm text-ink focus:border-brand focus:outline-none focus:ring-4 focus:ring-brand/15"
              />
            </div>

            <div className="space-y-1">
              <Label>选项（每行一个，选择题填写）</Label>
              <textarea
                rows={3}
                placeholder={'A. 选项一\nB. 选项二'}
                value={editing.options}
                onChange={(e) => setEditing({ ...editing, options: e.target.value })}
                className="w-full rounded-lg border border-line px-3 py-2 text-xs text-ink focus:border-brand focus:outline-none focus:ring-4 focus:ring-brand/15"
              />
            </div>

            <div className="space-y-1">
              <Label>标准答案</Label>
              <Input
                required
                placeholder="输入标准答案..."
                value={editing.answer}
                onChange={(e) => setEditing({ ...editing, answer: e.target.value })}
              />
            </div>

            <div className="space-y-1">
              <Label>题目解析（选填）</Label>
              <textarea
                rows={2}
                placeholder="记录解题思路或考点提示..."
                value={editing.explanation}
                onChange={(e) => setEditing({ ...editing, explanation: e.target.value })}
                className="w-full rounded-lg border border-line px-3 py-2 text-xs text-ink focus:border-brand focus:outline-none focus:ring-4 focus:ring-brand/15"
              />
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-3">
              <ToggleSwitch
                id="edit-q-pending"
                checked={Boolean(editing.isPendingCorrection)}
                onChange={(v) => setEditing({ ...editing, isPendingCorrection: v })}
                label="标记为待修正"
                description="待修正题目不会命中本地缓存"
              />
              <div className="flex gap-2">
                <Button type="button" variant="outline" size="sm" onClick={() => setEditing(null)}>取消</Button>
                {editing.id && editing.isPendingCorrection && (
                  <Button
                    type="button"
                    size="sm"
                    disabled={saving}
                    onClick={() => void saveQuestionPayload({ ...editing, isPendingCorrection: false })}
                  >
                    <CheckCircle2 className="h-3.5 w-3.5" />
                    保存并解除待修正
                  </Button>
                )}
                <Button type="submit" size="sm" variant={editing.id && editing.isPendingCorrection ? 'outline' : 'default'} disabled={saving}>
                  {saving ? '保存中...' : '保存题目'}
                </Button>
              </div>
            </div>
          </form>
        )}
      </ModalDialog>

      {/* 导入弹窗（支持选择本地文件 + 粘贴） */}
      <ModalDialog
        open={showImport}
        onClose={() => setShowImport(false)}
        title="批量导入题目"
        subtitle={`将导入到：${activeFolderId >= 0 ? activeFolderLabel : '默认文件夹'}`}
        maxWidth="max-w-lg"
      >
        <form onSubmit={handleImport} className="space-y-3.5">
          <div className="flex items-end justify-between gap-3">
            <div className="flex-1 space-y-1">
              <Label>数据格式</Label>
              <Select value={importFormat} onChange={(e) => setImportFormat(e.target.value)} className="h-9 text-xs">
                <option value="json">JSON 数组（question / answer / options / questionType）</option>
                <option value="csv">CSV 表格（表头含 question,answer,options,questionType）</option>
              </Select>
            </div>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => setImportContent(importFormat === 'json' ? SAMPLE_JSON : SAMPLE_CSV)}
            >
              填入示例
            </Button>
          </div>

          <div
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              handleImportFile(e.dataTransfer?.files?.[0]);
            }}
            className="flex items-center justify-between rounded-xl border border-dashed border-line bg-canvas px-4 py-3 text-xs"
          >
            <div className="flex items-center gap-2 text-body">
              <FileUp className="h-4 w-4 text-brand" />
              <span>拖拽 .json / .csv 文件到此处，或选择本地文件</span>
            </div>
            <Button type="button" size="sm" variant="outline" onClick={() => fileInputRef.current?.click()}>
              选择文件
            </Button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".json,.csv,application/json,text/csv"
              className="hidden"
              onChange={(e) => {
                handleImportFile(e.target.files?.[0]);
                e.target.value = '';
              }}
            />
          </div>

          <textarea
            rows={8}
            required
            placeholder={importFormat === 'json' ? SAMPLE_JSON : SAMPLE_CSV}
            value={importContent}
            onChange={(e) => setImportContent(e.target.value)}
            className="w-full rounded-xl border border-line p-3 font-mono text-xs text-ink focus:border-brand focus:outline-none focus:ring-4 focus:ring-brand/15"
          />

          <div className="flex justify-end gap-2 pt-1">
            <Button type="button" variant="outline" size="sm" onClick={() => setShowImport(false)}>取消</Button>
            <Button type="submit" size="sm" disabled={importing || !importContent.trim()}>
              {importing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
              {importing ? '导入中...' : '开始导入'}
            </Button>
          </div>
        </form>
      </ModalDialog>

      {/* 删除题目确认 */}
      <ConfirmDialog
        open={Boolean(idsToDelete?.length)}
        title="确认删除题目"
        description={`确定要永久删除选中的 ${idsToDelete?.length || 0} 道题目吗？此操作无法撤销。`}
        confirmText="确认删除"
        destructive
        loading={deleting}
        onCancel={() => setIdsToDelete(null)}
        onConfirm={() => void handleConfirmDeleteQuestions()}
      />

      {/* 删除文件夹确认 */}
      <ConfirmDialog
        open={Boolean(folderToDelete)}
        title={`删除文件夹「${folderToDelete?.name || ''}」`}
        description="默认情况下，该文件夹内的题目会自动保留并移回「默认文件夹」。"
        confirmText="删除文件夹"
        destructive
        loading={deleting}
        extraOption={(
          <ToggleSwitch
            id="del-folder-with-qs"
            checked={deleteFolderWithQs}
            onChange={setDeleteFolderWithQs}
            label="同时删除文件夹内的所有题目"
            description="勾选后将永久清空该目录题目，请谨慎操作"
          />
        )}
        onCancel={() => setFolderToDelete(null)}
        onConfirm={() => void handleConfirmDeleteFolder()}
      />
    </div>
  );
}
