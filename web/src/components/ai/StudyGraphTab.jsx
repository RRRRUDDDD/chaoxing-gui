import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import Button from '../ui/Button';
import Input from '../ui/Input';
import Label from '../ui/Label';
import Select from '../ui/Select';
import { aiApi } from '../../api/aiApi';
import { ModalDialog, ConfirmDialog, EmptyState, ToggleSwitch } from './aiShared';
import {
  Plus, Trash2, Sparkles, BrainCircuit, GitBranch, ZoomIn, ZoomOut,
  RotateCcw, Loader2, Maximize2, List, Network, Edit3, Check, X, Search,
  MessageSquare, Link2, Unlink,
} from 'lucide-react';

const MASTERY_STYLES = [
  { fill: '#F8FAFC', stroke: '#94A3B8', text: '#334155', badge: 'bg-slate-100 text-slate-600', label: '未学习' },
  { fill: '#FEF3C7', stroke: '#D97706', text: '#92400E', badge: 'bg-amber-100 text-amber-800', label: '生疏' },
  { fill: '#DBEAFE', stroke: '#2563EB', text: '#1E40AF', badge: 'bg-blue-100 text-blue-800', label: '熟悉' },
  { fill: '#DCFCE7', stroke: '#16A34A', text: '#166534', badge: 'bg-emerald-100 text-emerald-800', label: '已掌握' },
];

const NODE_W = 148;
const NODE_H = 52;
const VIEW_W = 900;
const VIEW_H = 500;

function layoutNodes(nodes) {
  if (!nodes.length) return { positions: new Map(), bounds: { minX: 0, maxX: VIEW_W, minY: 0, maxY: VIEW_H } };
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const depthMemo = new Map();
  const getDepth = (node, seen = new Set()) => {
    if (depthMemo.has(node.id)) return depthMemo.get(node.id);
    if (!node.parentId || !byId.has(node.parentId) || seen.has(node.id)) {
      depthMemo.set(node.id, 0);
      return 0;
    }
    seen.add(node.id);
    const d = getDepth(byId.get(node.parentId), seen) + 1;
    depthMemo.set(node.id, d);
    return d;
  };
  const layers = new Map();
  for (const n of nodes) {
    const d = getDepth(n);
    if (!layers.has(d)) layers.set(d, []);
    layers.get(d).push(n);
  }
  const positions = new Map();
  const sortedDepths = [...layers.keys()].sort((a, b) => a - b);
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;

  sortedDepths.forEach((d, rowIdx) => {
    const row = layers.get(d);
    const spacingX = 188;
    const totalW = (row.length - 1) * spacingX;
    const startX = VIEW_W / 2 - totalW / 2;
    row.forEach((n, colIdx) => {
      const x = startX + colIdx * spacingX;
      const y = 70 + rowIdx * 124;
      positions.set(n.id, { x, y });
      minX = Math.min(minX, x - NODE_W / 2);
      maxX = Math.max(maxX, x + NODE_W / 2);
      minY = Math.min(minY, y - NODE_H / 2);
      maxY = Math.max(maxY, y + NODE_H / 2);
    });
  });
  return { positions, bounds: { minX, maxX, minY, maxY } };
}

function computeFitTransform(bounds) {
  const pad = 48;
  const contentW = Math.max(120, bounds.maxX - bounds.minX);
  const contentH = Math.max(80, bounds.maxY - bounds.minY);
  const scaleX = (VIEW_W - pad * 2) / contentW;
  const scaleY = (VIEW_H - pad * 2) / contentH;
  const zoom = Math.max(0.42, Math.min(1.15, Number(Math.min(scaleX, scaleY).toFixed(2))));
  const cx = (bounds.minX + bounds.maxX) / 2;
  const cy = (bounds.minY + bounds.maxY) / 2;
  return {
    zoom,
    pan: {
      x: Math.round(VIEW_W / 2 - cx * zoom),
      y: Math.round(VIEW_H / 2 - cy * zoom),
    },
  };
}

export default function StudyGraphTab({
  isActive = true,
  intent,
  dataVersion = 0,
  onStatsChanged,
  onAskTutor,
  onToast,
}) {
  const [subjects, setSubjects] = useState([]);
  const [activeSubjectId, setActiveSubjectId] = useState(null);
  const [graph, setGraph] = useState(null);
  const [folders, setFolders] = useState([]);
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [viewMode, setViewMode] = useState('graph'); // 'graph' | 'list'
  const [nodeSearch, setNodeSearch] = useState('');
  const [autoNextDue, setAutoNextDue] = useState(true);

  // Pan & Zoom
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const dragRef = useRef(null);

  // Forms / dialogs
  const [newSubjectName, setNewSubjectName] = useState('');
  const [newSubjectDesc, setNewSubjectDesc] = useState('');
  const [showAddSubject, setShowAddSubject] = useState(false);

  const [showGenerate, setShowGenerate] = useState(false);
  const [genTargetSubjectId, setGenTargetSubjectId] = useState('current'); // 'current' | 'new'
  const [genNewSubjectName, setGenNewSubjectName] = useState('');
  const [genTopic, setGenTopic] = useState('');
  const [genFolderId, setGenFolderId] = useState('');
  const [generating, setGenerating] = useState(false);

  const [showNodeForm, setShowNodeForm] = useState(false);
  const [nodeDraft, setNodeDraft] = useState({ name: '', summary: '', parentId: '' });
  const [editingNode, setEditingNode] = useState(null);
  const [edgeTarget, setEdgeTarget] = useState('');
  const [edgeRelation, setEdgeRelation] = useState('关联');

  // Inline question-node linker
  const [showLinkPicker, setShowLinkPicker] = useState(false);
  const [linkQuery, setLinkQuery] = useState('');
  const [linkCandidates, setLinkCandidates] = useState([]);
  const [loadingCandidates, setLoadingCandidates] = useState(false);

  const [subjectToDelete, setSubjectToDelete] = useState(null);
  const [nodeToDelete, setNodeToDelete] = useState(null);
  const [errorMsg, setErrorMsg] = useState('');

  const [prevIntentTs, setPrevIntentTs] = useState(null);
  const syncedVerRef = useRef(dataVersion);

  // React to navigation intent from QuestionBank ("生成图谱")
  if (intent?._ts && intent._ts !== prevIntentTs) {
    setPrevIntentTs(intent._ts);
    if (intent.openGenerate) {
      if (intent.folderId !== undefined && intent.folderId !== null) {
        setGenFolderId(String(intent.folderId));
      }
      if (!activeSubjectId) {
        setGenTargetSubjectId('new');
        setGenNewSubjectName(intent.folderName || '');
      } else {
        setGenTargetSubjectId('current');
        if (intent.folderName) setGenNewSubjectName(intent.folderName);
      }
      setShowGenerate(true);
    }
  }

  const loadSubjects = useCallback(async () => {
    const [subRes, foldRes] = await Promise.all([aiApi.listSubjects(), aiApi.listFolders()]);
    const list = subRes.items || [];
    setSubjects(list);
    setFolders(foldRes.items || []);
    setActiveSubjectId((prev) => (prev && list.some((s) => s.id === prev) ? prev : list[0]?.id ?? null));
    return list;
  }, []);

  const loadGraph = useCallback(async (sid) => {
    if (!sid) { setGraph(null); return null; }
    try {
      const data = await aiApi.getStudyGraph(sid);
      setGraph(data);
      return data;
    } catch (err) {
      setErrorMsg(err.message);
      return null;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([aiApi.listSubjects(), aiApi.listFolders()])
      .then(([subRes, foldRes]) => {
        if (cancelled) return;
        const list = subRes.items || [];
        setSubjects(list);
        setFolders(foldRes.items || []);
        setActiveSubjectId((prev) => (prev && list.some((s) => s.id === prev) ? prev : list[0]?.id ?? null));
      })
      .catch((err) => { if (!cancelled) setErrorMsg(err.message); });
    return () => { cancelled = true; };
  }, []);

  // Refresh folders softly when returning to this tab
  useEffect(() => {
    if (isActive && dataVersion !== syncedVerRef.current) {
      syncedVerRef.current = dataVersion;
      aiApi.listFolders().then((res) => setFolders(res.items || [])).catch(() => {});
    }
  }, [isActive, dataVersion]);

  useEffect(() => {
    if (!activeSubjectId) return undefined;
    let cancelled = false;
    aiApi.getStudyGraph(activeSubjectId)
      .then((data) => { if (!cancelled) setGraph(data); })
      .catch((err) => { if (!cancelled) setErrorMsg(err.message); });
    return () => { cancelled = true; };
  }, [activeSubjectId]);

  // Search local questions when linker picker is open
  useEffect(() => {
    if (!showLinkPicker) return undefined;
    let cancelled = false;
    const t = setTimeout(() => {
      setLoadingCandidates(true);
      aiApi.listQuestions({ keyword: linkQuery.trim(), page: 1, pageSize: 12 })
        .then((res) => { if (!cancelled) setLinkCandidates(res.items || []); })
        .catch(() => {})
        .finally(() => { if (!cancelled) setLoadingCandidates(false); });
    }, 200);
    return () => { cancelled = true; clearTimeout(t); };
  }, [showLinkPicker, linkQuery]);

  const { positions, bounds } = useMemo(() => layoutNodes(graph?.nodes || []), [graph]);

  // Auto-fit when subject changes or graph is populated
  const prevGraphSubject = useRef(null);
  useEffect(() => {
    if (!graph?.nodes?.length) return;
    if (prevGraphSubject.current !== graph.subject?.id) {
      prevGraphSubject.current = graph.subject?.id;
      const fit = computeFitTransform(bounds);
      setZoom(fit.zoom);
      setPan(fit.pan);
    }
  }, [graph, bounds]);

  const handleFitView = useCallback(() => {
    if (!graph?.nodes?.length) {
      setZoom(1);
      setPan({ x: 0, y: 0 });
      return;
    }
    const fit = computeFitTransform(bounds);
    setZoom(fit.zoom);
    setPan(fit.pan);
  }, [graph, bounds]);

  const selectedNode = useMemo(
    () => (graph?.nodes || []).find((n) => n.id === selectedNodeId) || null,
    [graph, selectedNodeId]
  );

  const selectedNodeEdges = useMemo(() => {
    if (!selectedNode || !graph?.edges) return [];
    const byId = new Map((graph.nodes || []).map((n) => [n.id, n]));
    return graph.edges
      .filter((e) => e.fromId === selectedNode.id || e.toId === selectedNode.id)
      .map((e) => ({
        ...e,
        fromName: byId.get(e.fromId)?.name || `#${e.fromId}`,
        toName: byId.get(e.toId)?.name || `#${e.toId}`,
      }));
  }, [graph, selectedNode]);

  const dueNodes = useMemo(() => {
    return [...(graph?.nodes || [])]
      .sort((a, b) => (a.retention ?? 0) - (b.retention ?? 0))
      .slice(0, 8);
  }, [graph]);

  const filteredListNodes = useMemo(() => {
    const q = nodeSearch.trim().toLowerCase();
    const list = graph?.nodes || [];
    if (!q) return list;
    return list.filter(
      (n) => n.name.toLowerCase().includes(q) || (n.summary || '').toLowerCase().includes(q)
    );
  }, [graph, nodeSearch]);

  const focusNode = useCallback((nodeId) => {
    setSelectedNodeId(nodeId);
    setEditingNode(null);
    setShowLinkPicker(false);
    const pos = positions.get(nodeId);
    if (pos) {
      setPan({
        x: Math.round(VIEW_W / 2 - pos.x * zoom),
        y: Math.round(VIEW_H / 2 - pos.y * zoom),
      });
    }
  }, [positions, zoom]);

  const handleCreateSubject = async (e) => {
    e.preventDefault();
    if (!newSubjectName.trim()) return;
    try {
      const created = await aiApi.saveSubject({ name: newSubjectName.trim(), description: newSubjectDesc.trim() });
      setNewSubjectName('');
      setNewSubjectDesc('');
      setShowAddSubject(false);
      await loadSubjects();
      setActiveSubjectId(created.id);
      onStatsChanged?.();
      onToast?.(`已创建学科「${created.name}」`, 'success');
    } catch (err) {
      setErrorMsg(err.message);
    }
  };

  const handleConfirmDeleteSubject = async () => {
    if (!subjectToDelete) return;
    try {
      await aiApi.deleteSubject(subjectToDelete.id);
      if (activeSubjectId === subjectToDelete.id) setGraph(null);
      setSubjectToDelete(null);
      setSelectedNodeId(null);
      await loadSubjects();
      onStatsChanged?.();
      onToast?.('学科已删除', 'success');
    } catch (err) {
      setErrorMsg(err.message);
    }
  };

  const handleGenerate = async (e) => {
    e.preventDefault();
    if (generating) return;
    const creatingNew = !activeSubjectId || genTargetSubjectId === 'new';
    if (creatingNew && !genNewSubjectName.trim()) {
      setErrorMsg('请输入新学科名称');
      return;
    }
    setGenerating(true);
    setErrorMsg('');
    try {
      let targetSid = activeSubjectId;
      if (creatingNew) {
        const created = await aiApi.saveSubject({ name: genNewSubjectName.trim(), description: '' });
        targetSid = created.id;
        setActiveSubjectId(targetSid);
      }
      const res = await aiApi.generateStudyGraph({
        subjectId: targetSid,
        topic: genTopic.trim(),
        folderId: genFolderId === '' ? null : Number(genFolderId),
      });
      setGraph(res.graph);
      prevGraphSubject.current = null; // trigger auto-fit
      setShowGenerate(false);
      setGenTopic('');
      setGenNewSubjectName('');
      await loadSubjects();
      onStatsChanged?.();
      onToast?.(`AI 已生成 ${res.createdNodeCount} 个知识点（${res.modelName}）`, 'success');
    } catch (err) {
      setErrorMsg(err.message);
    } finally {
      setGenerating(false);
    }
  };

  const handleSaveNode = async (e) => {
    e.preventDefault();
    if (!activeSubjectId || !nodeDraft.name.trim()) return;
    try {
      await aiApi.saveGraphNode({
        subjectId: activeSubjectId,
        name: nodeDraft.name.trim(),
        summary: nodeDraft.summary.trim(),
        parentId: nodeDraft.parentId ? Number(nodeDraft.parentId) : null,
      });
      setNodeDraft({ name: '', summary: '', parentId: '' });
      setShowNodeForm(false);
      await Promise.all([loadGraph(activeSubjectId), loadSubjects()]);
      onStatsChanged?.();
      onToast?.('知识点已添加', 'success');
    } catch (err) {
      setErrorMsg(err.message);
    }
  };

  const handleSaveNodeEdit = async (e) => {
    e.preventDefault();
    if (!editingNode || !editingNode.name.trim() || !activeSubjectId) return;
    try {
      await aiApi.saveGraphNode({
        id: editingNode.id,
        subjectId: activeSubjectId,
        name: editingNode.name.trim(),
        summary: editingNode.summary.trim(),
        parentId: editingNode.parentId ?? null,
      });
      setEditingNode(null);
      await loadGraph(activeSubjectId);
      onToast?.('知识点已更新', 'success');
    } catch (err) {
      setErrorMsg(err.message);
    }
  };

  const handleConfirmDeleteNode = async () => {
    if (!nodeToDelete) return;
    try {
      await aiApi.deleteGraphNode(nodeToDelete.id);
      setNodeToDelete(null);
      setSelectedNodeId(null);
      setEditingNode(null);
      await Promise.all([loadGraph(activeSubjectId), loadSubjects()]);
      onStatsChanged?.();
      onToast?.('知识点已删除', 'success');
    } catch (err) {
      setErrorMsg(err.message);
    }
  };

  const handleAddEdge = async () => {
    if (!selectedNode || !edgeTarget || !activeSubjectId) return;
    try {
      await aiApi.saveGraphEdge({
        subjectId: activeSubjectId,
        fromId: selectedNode.id,
        toId: Number(edgeTarget),
        relation: edgeRelation.trim() || '关联',
      });
      setEdgeTarget('');
      await loadGraph(activeSubjectId);
      onToast?.('知识连边已建立', 'success');
    } catch (err) {
      setErrorMsg(err.message);
    }
  };

  const handleDeleteEdge = async (edgeId) => {
    try {
      await aiApi.deleteGraphEdge(edgeId);
      await loadGraph(activeSubjectId);
      onToast?.('关系连边已移除', 'info');
    } catch (err) {
      setErrorMsg(err.message);
    }
  };

  const handleToggleLinkQuestion = async (questionId, currentlyLinked) => {
    if (!selectedNode) return;
    try {
      await aiApi.linkQuestionNode({
        questionId,
        nodeId: selectedNode.id,
        unlink: currentlyLinked,
      });
      await loadGraph(activeSubjectId);
      onToast?.(currentlyLinked ? '已移除题目关联' : '已将题目关联到该知识点', 'success');
    } catch (err) {
      setErrorMsg(err.message);
    }
  };

  const handleReviewNode = async (quality) => {
    if (!selectedNode) return;
    const reviewedId = selectedNode.id;
    try {
      await aiApi.reviewGraphNode(reviewedId, quality);
      const refreshed = await loadGraph(activeSubjectId);
      if (autoNextDue && refreshed?.nodes?.length > 1) {
        const nextDue = [...refreshed.nodes]
          .filter((n) => n.id !== reviewedId)
          .sort((a, b) => (a.retention ?? 0) - (b.retention ?? 0))[0];
        if (nextDue) {
          setSelectedNodeId(nextDue.id);
          setEditingNode(null);
          setShowLinkPicker(false);
          onToast?.(`已打卡，自动切至下一个待复习节点「${nextDue.name}」`, 'success');
          return;
        }
      }
      onToast?.('已按艾宾浩斯遗忘曲线更新记忆留存率', 'success');
    } catch (err) {
      setErrorMsg(err.message);
    }
  };

  const handleWheel = (e) => {
    e.preventDefault();
    const delta = e.deltaY < 0 ? 0.12 : -0.12;
    setZoom((z) => Math.max(0.35, Math.min(2.2, Number((z + delta).toFixed(2)))));
  };

  const linkedQuestionIds = useMemo(
    () => new Set((selectedNode?.questions || []).map((q) => q.id)),
    [selectedNode]
  );

  return (
    <div className="space-y-4">
      {errorMsg && (
        <div role="alert" className="flex items-center justify-between rounded-xl border border-danger/25 bg-danger/5 px-4 py-2.5 text-xs text-danger">
          <span>{errorMsg}</span>
          <button type="button" onClick={() => setErrorMsg('')} className="underline">关闭</button>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-12">
        {/* 左侧：学科列表 + 待复习快捷列表 */}
        <aside className="space-y-4 lg:col-span-3">
          <div className="space-y-2.5 rounded-2xl border border-line bg-white p-3.5 shadow-card">
            <div className="flex items-center justify-between px-1">
              <h3 className="text-xs font-semibold text-ink">学习学科</h3>
              <button
                type="button"
                onClick={() => setShowAddSubject((v) => !v)}
                className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-brand hover:bg-brand-soft"
              >
                <Plus className="h-3.5 w-3.5" />
                新建
              </button>
            </div>

            {showAddSubject && (
              <form onSubmit={handleCreateSubject} className="space-y-2 rounded-xl border border-line bg-canvas p-2.5">
                <Input
                  value={newSubjectName}
                  onChange={(e) => setNewSubjectName(e.target.value)}
                  placeholder="学科名称（如：高等数学）"
                  className="h-8 text-xs"
                  autoFocus
                  required
                />
                <Input
                  value={newSubjectDesc}
                  onChange={(e) => setNewSubjectDesc(e.target.value)}
                  placeholder="简短说明（选填）"
                  className="h-8 text-xs"
                />
                <div className="flex gap-1.5">
                  <Button type="button" size="sm" variant="outline" className="flex-1" onClick={() => setShowAddSubject(false)}>取消</Button>
                  <Button type="submit" size="sm" className="flex-1">创建</Button>
                </div>
              </form>
            )}

            {subjects.length === 0 ? (
              <p className="py-5 text-center text-xs text-faint">点击「新建」或「AI 一键生成」开始</p>
            ) : (
              <div className="space-y-1">
                {subjects.map((s) => (
                  <div
                    key={s.id}
                    className={`group flex items-center justify-between rounded-xl px-2.5 py-2 text-xs transition ${
                      activeSubjectId === s.id ? 'bg-brand-soft font-medium text-brand' : 'text-body hover:bg-soft hover:text-ink'
                    }`}
                  >
                    <button
                      type="button"
                      onClick={() => { setActiveSubjectId(s.id); setSelectedNodeId(null); setEditingNode(null); }}
                      className="min-w-0 flex-1 text-left"
                    >
                      <p className="truncate">{s.name}</p>
                      <p className="text-[10px] text-faint tnum">{s.nodeCount} 节点 · {s.edgeCount} 连边</p>
                    </button>
                    <button
                      type="button"
                      onClick={() => setSubjectToDelete(s)}
                      title="删除学科"
                      className="hidden rounded p-1 text-faint hover:text-danger group-hover:inline-flex"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            )}

            {/* 图例 */}
            <div className="border-t border-line pt-2.5">
              <p className="mb-1.5 text-[11px] font-medium text-faint">掌握度图例（遗忘曲线实时衰减）</p>
              <div className="grid grid-cols-2 gap-1.5 text-[11px]">
                {MASTERY_STYLES.map((m) => (
                  <div key={m.label} className="flex items-center gap-1.5">
                    <span className="h-2.5 w-2.5 rounded-full border" style={{ background: m.fill, borderColor: m.stroke }} />
                    <span className="text-body">{m.label}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* 优先复习榜单 */}
          {dueNodes.length > 0 && (
            <div className="space-y-2 rounded-2xl border border-line bg-white p-3.5 shadow-card">
              <div className="flex items-center justify-between px-1">
                <p className="text-xs font-semibold text-ink">优先复习节点</p>
                <span className="text-[10px] text-faint">按留存率排序</span>
              </div>
              <div className="space-y-1">
                {dueNodes.map((n) => {
                  const pct = Math.round((n.retention || 0) * 100);
                  const active = selectedNodeId === n.id;
                  return (
                    <button
                      key={n.id}
                      type="button"
                      onClick={() => focusNode(n.id)}
                      className={`flex w-full items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs transition ${
                        active ? 'bg-brand-soft font-medium text-brand' : 'text-body hover:bg-soft'
                      }`}
                    >
                      <span className="truncate">{n.name}</span>
                      <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium tnum ${pct < 50 ? 'bg-warning/15 text-warning-ink' : 'bg-soft text-faint'}`}>
                        {pct}%
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </aside>

        {/* 右侧：图谱主画布 + 侧边详情面板 */}
        <div className="lg:col-span-9">
          {!graph ? (
            <EmptyState
              icon={BrainCircuit}
              title="选择或新建一个学科"
              description="可结合本地题库一键让 AI 生成可视化知识图谱，并跟踪艾宾浩斯遗忘曲线进行科学复习。"
              action={(
                <div className="flex flex-wrap items-center justify-center gap-2">
                  <Button
                    size="sm"
                    onClick={() => {
                      setGenTargetSubjectId('new');
                      setShowGenerate(true);
                    }}
                  >
                    <Sparkles className="h-3.5 w-3.5" />
                    AI 一键建学科并生成图谱
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => setShowAddSubject(true)}>
                    <Plus className="h-3.5 w-3.5" />
                    手动新建学科
                  </Button>
                </div>
              )}
            />
          ) : (
            <div className="space-y-3.5 rounded-2xl border border-line bg-white p-4 shadow-card">
              {/* 顶部工具栏 */}
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h2 className="text-sm font-semibold text-ink">{graph.subject.name} · 知识图谱</h2>
                  {graph.subject.description && <p className="text-xs text-faint">{graph.subject.description}</p>}
                </div>
                <div className="flex flex-wrap items-center gap-1.5">
                  <div className="inline-flex rounded-lg border border-line bg-canvas p-0.5 text-xs">
                    <button
                      type="button"
                      onClick={() => setViewMode('graph')}
                      className={`inline-flex items-center gap-1 rounded-md px-2.5 py-1 font-medium ${viewMode === 'graph' ? 'bg-white text-ink shadow-xs' : 'text-faint hover:text-ink'}`}
                    >
                      <Network className="h-3.5 w-3.5" />
                      图谱
                    </button>
                    <button
                      type="button"
                      onClick={() => setViewMode('list')}
                      className={`inline-flex items-center gap-1 rounded-md px-2.5 py-1 font-medium ${viewMode === 'list' ? 'bg-white text-ink shadow-xs' : 'text-faint hover:text-ink'}`}
                    >
                      <List className="h-3.5 w-3.5" />
                      列表
                    </button>
                  </div>

                  <Button
                    size="sm"
                    onClick={() => {
                      setGenTargetSubjectId('current');
                      setShowGenerate(true);
                    }}
                  >
                    <Sparkles className="h-3.5 w-3.5" />
                    AI 一键生成
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => setShowNodeForm((v) => !v)}>
                    <Plus className="h-3.5 w-3.5" />
                    添加节点
                  </Button>
                </div>
              </div>

              {showNodeForm && (
                <form onSubmit={handleSaveNode} className="grid gap-2 rounded-xl border border-line bg-canvas p-3 sm:grid-cols-12">
                  <Input
                    className="h-9 text-xs sm:col-span-3"
                    placeholder="知识点名称"
                    value={nodeDraft.name}
                    onChange={(e) => setNodeDraft({ ...nodeDraft, name: e.target.value })}
                    required
                  />
                  <Input
                    className="h-9 text-xs sm:col-span-4"
                    placeholder="核心概括（选填）"
                    value={nodeDraft.summary}
                    onChange={(e) => setNodeDraft({ ...nodeDraft, summary: e.target.value })}
                  />
                  <Select
                    className="h-9 text-xs sm:col-span-3"
                    value={nodeDraft.parentId}
                    onChange={(e) => setNodeDraft({ ...nodeDraft, parentId: e.target.value })}
                  >
                    <option value="">无父节点（顶层）</option>
                    {graph.nodes.map((n) => <option key={n.id} value={n.id}>父：{n.name}</option>)}
                  </Select>
                  <Button type="submit" size="sm" className="h-9 sm:col-span-2">保存节点</Button>
                </form>
              )}

              {/* 画布/列表 + 侧边检查器 */}
              <div className="grid gap-4 xl:grid-cols-12">
                <div className={selectedNode ? 'xl:col-span-7' : 'xl:col-span-12'}>
                  {viewMode === 'graph' ? (
                    <div className="relative">
                      {/* 浮动缩放条 */}
                      {graph.nodes.length > 0 && (
                        <div className="absolute right-3 top-3 z-10 flex items-center gap-1 rounded-xl border border-line bg-white/95 p-1 shadow-card backdrop-blur-sm">
                          <button
                            type="button"
                            onClick={() => setZoom((z) => Math.max(0.35, +(z - 0.15).toFixed(2)))}
                            title="缩小"
                            className="rounded-lg p-1.5 text-body hover:bg-soft hover:text-ink"
                          >
                            <ZoomOut className="h-3.5 w-3.5" />
                          </button>
                          <span className="px-1 text-[11px] font-medium text-faint tnum">{Math.round(zoom * 100)}%</span>
                          <button
                            type="button"
                            onClick={() => setZoom((z) => Math.min(2.2, +(z + 0.15).toFixed(2)))}
                            title="放大"
                            className="rounded-lg p-1.5 text-body hover:bg-soft hover:text-ink"
                          >
                            <ZoomIn className="h-3.5 w-3.5" />
                          </button>
                          <button
                            type="button"
                            onClick={handleFitView}
                            title="自适应居中"
                            className="rounded-lg p-1.5 text-body hover:bg-soft hover:text-ink"
                          >
                            <Maximize2 className="h-3.5 w-3.5" />
                          </button>
                          <button
                            type="button"
                            onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}
                            title="重置视角"
                            className="rounded-lg p-1.5 text-body hover:bg-soft hover:text-ink"
                          >
                            <RotateCcw className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      )}

                      <div
                        className="relative h-[480px] w-full cursor-grab select-none overflow-hidden rounded-xl border border-line bg-canvas active:cursor-grabbing"
                        onWheel={handleWheel}
                        onMouseDown={(e) => {
                          dragRef.current = { startX: e.clientX, startY: e.clientY, panX: pan.x, panY: pan.y, moved: false };
                        }}
                        onMouseMove={(e) => {
                          if (!dragRef.current) return;
                          const dx = e.clientX - dragRef.current.startX;
                          const dy = e.clientY - dragRef.current.startY;
                          if (Math.abs(dx) > 4 || Math.abs(dy) > 4) dragRef.current.moved = true;
                          setPan({ x: dragRef.current.panX + dx, y: dragRef.current.panY + dy });
                        }}
                        onMouseUp={() => { dragRef.current = null; }}
                        onMouseLeave={() => { dragRef.current = null; }}
                      >
                        {graph.nodes.length === 0 ? (
                          <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-xs text-faint">
                            <BrainCircuit className="h-8 w-8 text-faint/40" />
                            <p>暂无知识点，点击右上角「AI 一键生成」或「添加节点」开始构建</p>
                          </div>
                        ) : (
                          <svg className="h-full w-full" viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}>
                            <defs>
                              <marker id="ai-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                                <path d="M 0 1 L 10 5 L 0 9 z" fill="#94A3B8" />
                              </marker>
                            </defs>
                            <g transform={`translate(${pan.x}, ${pan.y}) scale(${zoom})`}>
                              {/* Smooth curved edges */}
                              {graph.edges.map((edge) => {
                                const from = positions.get(edge.fromId);
                                const to = positions.get(edge.toId);
                                if (!from || !to) return null;
                                const isRelated = selectedNodeId === edge.fromId || selectedNodeId === edge.toId;
                                const dy = to.y - from.y;
                                const startY = from.y + (dy >= 0 ? NODE_H / 2 : -NODE_H / 2);
                                const endY = to.y + (dy >= 0 ? -NODE_H / 2 - 4 : NODE_H / 2 + 4);
                                const cy = (startY + endY) / 2;
                                const d = `M ${from.x} ${startY} C ${from.x} ${cy}, ${to.x} ${cy}, ${to.x} ${endY}`;
                                const mx = (from.x + to.x) / 2;
                                const my = cy;
                                return (
                                  <g key={edge.id}>
                                    <path
                                      d={d}
                                      fill="none"
                                      stroke={isRelated ? '#2563EB' : '#CBD5E1'}
                                      strokeWidth={isRelated ? 2.2 : 1.6}
                                      markerEnd="url(#ai-arrow)"
                                    />
                                    {edge.relation && (
                                      <g transform={`translate(${mx}, ${my})`}>
                                        <rect x="-24" y="-9" width="48" height="18" rx="9" fill="#FFFFFF" stroke={isRelated ? '#93C5FD' : '#E2E8F0'} strokeWidth="1" />
                                        <text y="3.5" textAnchor="middle" fontSize="9.5" fill={isRelated ? '#1D4ED8' : '#64748B'}>
                                          {edge.relation.slice(0, 5)}
                                        </text>
                                      </g>
                                    )}
                                  </g>
                                );
                              })}

                              {/* Nodes */}
                              {graph.nodes.map((node) => {
                                const pos = positions.get(node.id);
                                if (!pos) return null;
                                const style = MASTERY_STYLES[node.mastery] || MASTERY_STYLES[0];
                                const isSelected = selectedNodeId === node.id;
                                const pct = Math.round((node.retention || 0) * 100);
                                return (
                                  <g
                                    key={node.id}
                                    transform={`translate(${pos.x}, ${pos.y})`}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      if (dragRef.current?.moved) return;
                                      setSelectedNodeId(node.id);
                                      setEditingNode(null);
                                      setShowLinkPicker(false);
                                    }}
                                    className="cursor-pointer"
                                  >
                                    <rect
                                      x={-NODE_W / 2}
                                      y={-NODE_H / 2}
                                      width={NODE_W}
                                      height={NODE_H}
                                      rx="12"
                                      fill={style.fill}
                                      stroke={isSelected ? '#1D4ED8' : style.stroke}
                                      strokeWidth={isSelected ? 2.6 : 1.4}
                                    />
                                    <text y="-4" textAnchor="middle" fontSize="12" fontWeight="600" fill={style.text}>
                                      {node.name.length > 10 ? `${node.name.slice(0, 9)}…` : node.name}
                                    </text>
                                    <text y="13" textAnchor="middle" fontSize="10" fill="#64748B">
                                      阶段 {node.forgettingStage}/6 · 留存 {pct}%
                                    </text>
                                  </g>
                                );
                              })}
                            </g>
                          </svg>
                        )}
                        <span className="pointer-events-none absolute bottom-2.5 left-3 rounded-md bg-white/80 px-2 py-0.5 text-[10px] text-faint">
                          滚轮缩放 · 拖拽画布 · 点击节点复习、联题与问助教
                        </span>
                      </div>
                    </div>
                  ) : (
                    /* 列表视图 */
                    <div className="space-y-2.5">
                      <div className="relative">
                        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-faint" />
                        <Input
                          value={nodeSearch}
                          onChange={(e) => setNodeSearch(e.target.value)}
                          placeholder="搜索知识点名称或概括..."
                          className="h-9 pl-8 text-xs"
                        />
                      </div>
                      <div className="max-h-[430px] divide-y divide-line overflow-y-auto rounded-xl border border-line">
                        {filteredListNodes.length === 0 ? (
                          <p className="py-10 text-center text-xs text-faint">暂无匹配的知识点</p>
                        ) : (
                          filteredListNodes.map((node) => {
                            const style = MASTERY_STYLES[node.mastery] || MASTERY_STYLES[0];
                            const pct = Math.round((node.retention || 0) * 100);
                            const active = selectedNodeId === node.id;
                            return (
                              <button
                                key={node.id}
                                type="button"
                                onClick={() => { setSelectedNodeId(node.id); setEditingNode(null); setShowLinkPicker(false); }}
                                className={`flex w-full items-center justify-between gap-3 px-4 py-3 text-left text-xs transition ${
                                  active ? 'bg-brand-soft/60' : 'bg-white hover:bg-canvas'
                                }`}
                              >
                                <div className="min-w-0 flex-1">
                                  <div className="flex items-center gap-2">
                                    <span className="font-semibold text-ink">{node.name}</span>
                                    <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${style.badge}`}>{style.label}</span>
                                  </div>
                                  {node.summary && <p className="mt-0.5 truncate text-faint">{node.summary}</p>}
                                </div>
                                <div className="shrink-0 text-right tnum">
                                  <p className="font-semibold text-ink">{pct}%</p>
                                  <p className="text-[10px] text-faint">阶段 {node.forgettingStage}/6</p>
                                </div>
                              </button>
                            );
                          })
                        )}
                      </div>
                    </div>
                  )}
                </div>

                {/* 右侧节点详情与遗忘曲线检查器 */}
                {selectedNode && (
                  <div className="flex flex-col space-y-3.5 rounded-xl border border-line bg-canvas/50 p-4 xl:col-span-5">
                    <div className="flex items-start justify-between gap-2">
                      {editingNode ? (
                        <form onSubmit={handleSaveNodeEdit} className="w-full space-y-2">
                          <Input
                            value={editingNode.name}
                            onChange={(e) => setEditingNode({ ...editingNode, name: e.target.value })}
                            className="h-8 text-xs font-medium"
                            required
                          />
                          <textarea
                            rows={2}
                            value={editingNode.summary}
                            onChange={(e) => setEditingNode({ ...editingNode, summary: e.target.value })}
                            placeholder="知识点核心概括..."
                            className="w-full rounded-lg border border-line bg-white px-2.5 py-1.5 text-xs focus:border-brand focus:outline-none"
                          />
                          <div className="flex justify-end gap-1.5">
                            <Button type="button" size="sm" variant="outline" onClick={() => setEditingNode(null)}>取消</Button>
                            <Button type="submit" size="sm"><Check className="h-3.5 w-3.5" />保存</Button>
                          </div>
                        </form>
                      ) : (
                        <>
                          <div className="min-w-0">
                            <h3 className="text-sm font-semibold text-ink">{selectedNode.name}</h3>
                            <p className="mt-1 text-xs leading-relaxed text-body">
                              {selectedNode.summary || '暂无概括，可点击右侧编辑补充核心笔记。'}
                            </p>
                          </div>
                          <div className="flex shrink-0 items-center gap-0.5">
                            <button
                              type="button"
                              onClick={() =>
                                onAskTutor?.({
                                  contextBadge: `知识点 · ${graph.subject.name}`,
                                  contextTitle: selectedNode.name,
                                  subjectId: activeSubjectId,
                                  autoSend: true,
                                  prompt: `请用通俗易懂的方式帮我复习学科「${graph.subject.name}」的知识点「${selectedNode.name}」${selectedNode.summary ? `（当前笔记：${selectedNode.summary}）` : ''}，总结核心考点、易混概念，并给出一道自测例题。`,
                                })
                              }
                              title="用快捷 AI 助教讲解该知识点"
                              className="rounded-lg p-1.5 text-brand hover:bg-brand-soft"
                            >
                              <MessageSquare className="h-3.5 w-3.5" />
                            </button>
                            <button
                              type="button"
                              onClick={() => setEditingNode({ id: selectedNode.id, name: selectedNode.name, summary: selectedNode.summary || '', parentId: selectedNode.parentId })}
                              title="编辑知识点"
                              className="rounded-lg p-1.5 text-faint hover:bg-white hover:text-brand"
                            >
                              <Edit3 className="h-3.5 w-3.5" />
                            </button>
                            <button
                              type="button"
                              onClick={() => setNodeToDelete(selectedNode)}
                              title="删除节点"
                              className="rounded-lg p-1.5 text-faint hover:bg-white hover:text-danger"
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                            <button
                              type="button"
                              onClick={() => setSelectedNodeId(null)}
                              title="关闭详情面板"
                              className="rounded-lg p-1.5 text-faint hover:bg-white hover:text-ink"
                            >
                              <X className="h-3.5 w-3.5" />
                            </button>
                          </div>
                        </>
                      )}
                    </div>

                    {/* 遗忘曲线连贯打卡 */}
                    <div className="space-y-2.5 rounded-xl border border-line bg-white p-3.5">
                      <div className="flex items-center justify-between text-xs">
                        <span className="font-semibold text-ink tnum">
                          记忆留存 {Math.round((selectedNode.retention || 0) * 100)}%
                        </span>
                        <span className="text-[11px] text-faint tnum">
                          阶段 {selectedNode.forgettingStage}/6
                          {selectedNode.nextReviewDue ? ` · 下次 ${selectedNode.nextReviewDue.slice(0, 10)}` : ''}
                        </span>
                      </div>
                      <div className="h-2 w-full overflow-hidden rounded-full bg-soft">
                        <div
                          className="h-full rounded-full bg-brand transition-all"
                          style={{ width: `${Math.max(4, Math.round((selectedNode.retention || 0) * 100))}%` }}
                        />
                      </div>
                      <div className="grid grid-cols-3 gap-1.5 pt-1">
                        <Button size="sm" variant="outline" onClick={() => void handleReviewNode('poor')} className="text-xs">
                          不会
                        </Button>
                        <Button size="sm" variant="outline" onClick={() => void handleReviewNode('fair')} className="text-xs">
                          有印象
                        </Button>
                        <Button size="sm" onClick={() => void handleReviewNode('good')} className="text-xs">
                          熟练掌握
                        </Button>
                      </div>
                      {graph.nodes.length > 1 && (
                        <div className="border-t border-line/70 pt-2">
                          <ToggleSwitch
                            id="study-auto-next-due"
                            checked={autoNextDue}
                            onChange={setAutoNextDue}
                            label="打卡后自动跳下一个待复习节点"
                          />
                        </div>
                      )}
                    </div>

                    {/* 关系连边管理 */}
                    <div className="space-y-2.5 rounded-xl border border-line bg-white p-3.5 text-xs">
                      <p className="flex items-center gap-1.5 font-semibold text-ink">
                        <GitBranch className="h-3.5 w-3.5 text-brand" />
                        知识关联（{selectedNodeEdges.length}）
                      </p>
                      {selectedNodeEdges.length > 0 && (
                        <div className="space-y-1">
                          {selectedNodeEdges.map((edge) => (
                            <div key={edge.id} className="flex items-center justify-between rounded-lg bg-canvas px-2.5 py-1.5">
                              <span className="truncate text-body">
                                {edge.fromName} <span className="text-brand">→ {edge.relation || '关联'} →</span> {edge.toName}
                              </span>
                              <button
                                type="button"
                                onClick={() => void handleDeleteEdge(edge.id)}
                                title="移除连边"
                                className="ml-2 shrink-0 text-faint hover:text-danger"
                              >
                                <X className="h-3.5 w-3.5" />
                              </button>
                            </div>
                          ))}
                        </div>
                      )}
                      <div className="grid grid-cols-12 gap-1.5">
                        <Select value={edgeTarget} onChange={(e) => setEdgeTarget(e.target.value)} className="col-span-6 h-8 text-xs">
                          <option value="">连接到节点...</option>
                          {graph.nodes.filter((n) => n.id !== selectedNode.id).map((n) => (
                            <option key={n.id} value={n.id}>{n.name}</option>
                          ))}
                        </Select>
                        <Input
                          value={edgeRelation}
                          onChange={(e) => setEdgeRelation(e.target.value)}
                          placeholder="关系"
                          className="col-span-3 h-8 text-xs"
                        />
                        <Button size="sm" variant="outline" className="col-span-3 h-8" onClick={handleAddEdge} disabled={!edgeTarget}>
                          连接
                        </Button>
                      </div>
                    </div>

                    {/* 关联题库题目（支持原地搜索关联与解除） */}
                    <div className="space-y-2 rounded-xl border border-line bg-white p-3.5 text-xs">
                      <div className="flex items-center justify-between">
                        <p className="flex items-center gap-1.5 font-semibold text-ink">
                          <Link2 className="h-3.5 w-3.5 text-brand" />
                          关联题库题目（{selectedNode.questions?.length || 0}）
                        </p>
                        <button
                          type="button"
                          onClick={() => setShowLinkPicker((v) => !v)}
                          className="text-[11px] font-medium text-brand hover:underline"
                        >
                          {showLinkPicker ? '收起选词' : '+ 关联题目'}
                        </button>
                      </div>

                      {showLinkPicker && (
                        <div className="space-y-2 rounded-lg border border-line bg-canvas p-2.5">
                          <div className="relative">
                            <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-faint" />
                            <Input
                              value={linkQuery}
                              onChange={(e) => setLinkQuery(e.target.value)}
                              placeholder="搜索本地题库并一键关联..."
                              className="h-8 bg-white pl-7 text-xs"
                            />
                          </div>
                          {loadingCandidates ? (
                            <p className="py-2 text-center text-[11px] text-faint">搜索中...</p>
                          ) : linkCandidates.length === 0 ? (
                            <p className="py-2 text-center text-[11px] text-faint">暂无匹配题目</p>
                          ) : (
                            <div className="max-h-36 divide-y divide-line overflow-y-auto rounded-lg border border-line bg-white">
                              {linkCandidates.map((q) => {
                                const linked = linkedQuestionIds.has(q.id);
                                return (
                                  <div key={q.id} className="flex items-center justify-between gap-2 px-2.5 py-1.5">
                                    <span className="truncate text-[11px] text-ink">{q.question}</span>
                                    <button
                                      type="button"
                                      onClick={() => void handleToggleLinkQuestion(q.id, linked)}
                                      className={`shrink-0 rounded px-2 py-0.5 text-[10px] font-medium transition ${
                                        linked
                                          ? 'bg-success/10 text-success-ink hover:bg-danger/10 hover:text-danger'
                                          : 'bg-brand-soft text-brand hover:bg-brand hover:text-white'
                                      }`}
                                    >
                                      {linked ? '已关联' : '关联'}
                                    </button>
                                  </div>
                                );
                              })}
                            </div>
                          )}
                        </div>
                      )}

                      {selectedNode.questions?.length > 0 ? (
                        <div className="max-h-40 divide-y divide-line overflow-y-auto rounded-lg border border-line bg-canvas/40">
                          {selectedNode.questions.map((q) => (
                            <div key={q.id} className="flex items-start justify-between gap-2 px-3 py-2">
                              <div className="min-w-0 flex-1">
                                <p className="font-medium text-ink">{q.question}</p>
                                <p className="mt-0.5 text-success-ink">答：{q.answer}</p>
                              </div>
                              <button
                                type="button"
                                onClick={() => void handleToggleLinkQuestion(q.id, true)}
                                title="取消关联此题"
                                className="shrink-0 rounded p-1 text-faint hover:bg-white hover:text-danger"
                              >
                                <Unlink className="h-3 w-3" />
                              </button>
                            </div>
                          ))}
                        </div>
                      ) : !showLinkPicker && (
                        <p className="text-[11px] text-faint">暂未关联题目，点击右上角「+ 关联题目」可绑定本地原题。</p>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* AI 生成知识图谱弹窗（支持直接新建学科并生成，一步到位） */}
      <ModalDialog
        open={showGenerate}
        onClose={() => setShowGenerate(false)}
        title="AI 一键生成知识图谱"
        subtitle="基于课程大纲或本地题库自动抽取核心知识点与依赖关系"
        maxWidth="max-w-md"
      >
        <form onSubmit={handleGenerate} className="space-y-3.5">
          {activeSubjectId && (
            <div className="space-y-1">
              <Label>生成目标学科</Label>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <button
                  type="button"
                  onClick={() => setGenTargetSubjectId('current')}
                  className={`rounded-lg border px-3 py-2 text-left font-medium transition ${
                    genTargetSubjectId === 'current'
                      ? 'border-brand bg-brand-soft text-brand'
                      : 'border-line bg-white text-body hover:bg-canvas'
                  }`}
                >
                  当前学科：{graph?.subject?.name || ''}
                </button>
                <button
                  type="button"
                  onClick={() => setGenTargetSubjectId('new')}
                  className={`rounded-lg border px-3 py-2 text-left font-medium transition ${
                    genTargetSubjectId === 'new'
                      ? 'border-brand bg-brand-soft text-brand'
                      : 'border-line bg-white text-body hover:bg-canvas'
                  }`}
                >
                  + 新建学科并生成
                </button>
              </div>
            </div>
          )}

          {(!activeSubjectId || genTargetSubjectId === 'new') && (
            <div className="space-y-1">
              <Label>新学科名称</Label>
              <Input
                value={genNewSubjectName}
                onChange={(e) => setGenNewSubjectName(e.target.value)}
                placeholder="如：计算机网络 / 操作系统"
                required
              />
            </div>
          )}

          <div className="space-y-1">
            <Label>聚焦主题或课程章节大纲（选填）</Label>
            <Input
              value={genTopic}
              onChange={(e) => setGenTopic(e.target.value)}
              placeholder="如：极限与连续、导数应用..."
            />
          </div>
          <div className="space-y-1">
            <Label>结合本地题库文件夹（自动抽取考点并关联题目）</Label>
            <Select value={genFolderId} onChange={(e) => setGenFolderId(e.target.value)}>
              <option value="">不关联特定题库文件夹（纯大纲生成）</option>
              {folders.map((f) => <option key={f.id} value={f.id}>{f.name} ({f.questionCount} 题)</option>)}
            </Select>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="outline" size="sm" onClick={() => setShowGenerate(false)}>取消</Button>
            <Button type="submit" size="sm" disabled={generating}>
              {generating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
              {generating ? 'AI 生成中...' : '开始生成'}
            </Button>
          </div>
        </form>
      </ModalDialog>

      <ConfirmDialog
        open={Boolean(subjectToDelete)}
        title={`删除学科「${subjectToDelete?.name || ''}」`}
        description="将同时删除该学科下的所有知识节点与复习记录，此操作不可撤销。"
        confirmText="确认删除"
        destructive
        onCancel={() => setSubjectToDelete(null)}
        onConfirm={() => void handleConfirmDeleteSubject()}
      />

      <ConfirmDialog
        open={Boolean(nodeToDelete)}
        title={`删除知识点「${nodeToDelete?.name || ''}」`}
        description="确认删除此知识节点及其关联连边吗？关联的本地题库题目不会被删除。"
        confirmText="删除节点"
        destructive
        onCancel={() => setNodeToDelete(null)}
        onConfirm={() => void handleConfirmDeleteNode()}
      />
    </div>
  );
}
