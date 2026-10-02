import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import Button from './ui/Button';
import Label from './ui/Label';
import Select from './ui/Select';
import NumberInput from './ui/NumberInput';
import AdvancedSettings from './AdvancedSettings';
import TikuConfigEditor from './TikuConfigEditor';
import CourseToolSettings, { validateVisits } from './CourseToolSettings';
import RepositoryLink from './RepositoryLink';
import {
  BookOpen, SlidersHorizontal, LogOut, Play, Save, Loader2,
  Check, Search, GraduationCap, AlertCircle, BookX,
} from 'lucide-react';
import api from '../api/axios';
import { configSnapshot, defaultSettings, normalizeCourses, restoreCourseSelection, restoreSettings } from '../lib/courseSelection';

const CourseSelection = ({ userInfo, onStartStudy, onLogout, starting, loggingOut = false, startError, activeTaskId, taskRunning = false, onReturnToTask, preview = false }) => {
  const [courses, setCourses] = useState([]);
  const [selectedCourses, setSelectedCourses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState(null);
  const [savedSnapshot, setSavedSnapshot] = useState(null);
  const [editingTiku, setEditingTiku] = useState(false);
  const editorTrigger = useRef(null);
  const [query, setQuery] = useState('');
  const [settings, setSettings] = useState(defaultSettings);
  const [taskType, setTaskType] = useState('study');
  const [toolOptions, setToolOptions] = useState({ count: '10', interval: '30' });
  const [loadError, setLoadError] = useState('');
  const [selectionNotice, setSelectionNotice] = useState('');
  const [invalidFields, setInvalidFields] = useState({});
  const setFieldValid = useCallback((field, valid) => {
    setInvalidFields((current) => {
      if (Boolean(current[field]) === !valid) return current;
      const next = { ...current };
      if (valid) delete next[field];
      else next[field] = true;
      return next;
    });
  }, []);
  const [loadedAccount, setLoadedAccount] = useState(null);
  const [loadVersion, setLoadVersion] = useState(0);
  const saveController = useRef(null);
  const username = userInfo.username;
  const password = userInfo.password;
  const useCookies = userInfo.use_cookies === true;

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setCourses([]);
    setSelectedCourses([]);
    setSettings(defaultSettings());
    setTaskType('study');
    setToolOptions({ count: '10', interval: '30' });
    setLoadError('');
    setSaveStatus(null);
    setSaving(false);
    setSavedSnapshot(null);
    setEditingTiku(false);
    saveController.current?.abort();
    setQuery('');
    setSelectionNotice('');
    if (preview) {
      setCourses([
        { courseId: 'preview-001', title: '大学英语（演示课程）' },
        { courseId: 'preview-002', title: '高等数学（演示课程）' },
        { courseId: 'preview-003', title: '计算机基础（演示课程）' },
      ]);
      setSavedSnapshot(configSnapshot(defaultSettings(), username, []));
      setLoadedAccount(username);
      setSelectionNotice('请至少选择一门课程后开始学习');
      setLoading(false);
      return () => { controller.abort(); saveController.current?.abort(); };
    }
    const load = async () => {
      try {
        const results = await Promise.allSettled([
          api.get('/config', { signal: controller.signal }),
          api.post('/courses', { username, password, use_cookies: useCookies }, { signal: controller.signal }),
        ]);
        if (controller.signal.aborted) return;
        const [configResult, courseResult] = results;
        if (courseResult.status === 'rejected') throw courseResult.reason;
        if (!courseResult.value.data.status) throw new Error(courseResult.value.data.msg || '获取课程列表失败');
        const nextCourses = normalizeCourses(courseResult.value.data.data);
        setCourses(nextCourses);
        if (configResult.status === 'rejected') throw new Error('加载已保存配置失败，请重试');
        if (!configResult.value.data.status) throw new Error(configResult.value.data.msg || '加载已保存配置失败');
        const config = configResult.value.data.data || {};
        const selection = restoreCourseSelection(config, username, nextCourses);
        const restoredSettings = restoreSettings(config.settings);
        setSettings(restoredSettings);
        setSavedSnapshot(configSnapshot(restoredSettings, username, selection.ids));
        setSelectedCourses(selection.ids);
        setSelectionNotice(selection.saved
          ? selection.ids.length ? '已恢复此账号仍有效的课程选择，可继续调整' : '保存的课程选择为空或已失效，请重新选择课程'
          : '请勾选需要学习的课程');
      } catch (error) {
        if (!controller.signal.aborted) setLoadError(error.response?.data?.msg || error.message || '获取课程失败，请重试');
      } finally {
        if (!controller.signal.aborted) { setLoadedAccount(username); setLoading(false); }
      }
    };
    load();
    return () => { controller.abort(); saveController.current?.abort(); };
  }, [username, password, useCookies, preview, loadVersion]);

  const currentSnapshot = useMemo(() => configSnapshot(settings, username, selectedCourses), [settings, username, selectedCourses]);
  const dirty = savedSnapshot !== null && currentSnapshot !== savedSnapshot;
  useEffect(() => { setSaveStatus(null); }, [currentSnapshot]);
  useEffect(() => {
    if (!editingTiku) editorTrigger.current?.focus();
  }, [editingTiku]);

  const toggleCourse = (courseId) => {
    setSelectedCourses((prev) =>
      prev.includes(courseId)
        ? prev.filter((id) => id !== courseId)
        : [...prev, courseId]
    );
  };

  const filteredCourses = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return courses;
    return courses.filter(
      (c) => c.title?.toLowerCase().includes(q) || String(c.courseId).includes(q)
    );
  }, [courses, query]);

  const selectedCount = selectedCourses.length;
  const canSelect = !loading && loadedAccount === username && !loadError && !loggingOut;
  const validOptions = taskType !== 'visits' || Object.values(validateVisits(toolOptions)).every((error) => !error);
  const canStart = canSelect && courses.length > 0 && selectedCount > 0 && validOptions && Object.keys(invalidFields).length === 0 && !starting && !taskRunning;
  const startLabel = { study: '开始学习', visits: '开始提交次数', video_time: '读取视频列表', reading_time: '读取阅读任务', download: '读取资源列表' }[taskType];

  const selectAllVisible = useCallback(() => {
    if (canSelect) setSelectedCourses((selected) => [...new Set([...selected, ...filteredCourses.map((course) => course.courseId)])]);
  }, [canSelect, filteredCourses]);

  useEffect(() => {
    if (!canSelect || !filteredCourses.length) return undefined;
    const onKeyDown = (event) => {
      if (event.defaultPrevented || !(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey || event.key.toLowerCase() !== 'a') return;
      if (event.target?.closest?.('input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"]')) return;
      event.preventDefault();
      selectAllVisible();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [canSelect, filteredCourses.length, selectAllVisible]);

  const handleStartStudy = () => {
    if (!canStart) return;
    if (taskType === 'study') {
      onStartStudy({ ...settings, course_list: selectedCourses });
    } else if (taskType === 'visits') {
      onStartStudy({
        task_type: 'visits', course_list: selectedCourses,
        tool_options: { count: Number(toolOptions.count), interval: Number(toolOptions.interval) },
      });
    } else {
      onStartStudy({ task_type: 'catalog', course_list: selectedCourses, tool_options: { purpose: taskType } });
    }
  };

  const handleSaveConfig = async () => {
    if (loggingOut || loading || saving || loadError || loadedAccount !== username) return;
    const snapshot = currentSnapshot;
    if (preview) {
      setSavedSnapshot(snapshot);
      setSaveStatus({ type: 'success', message: '演示配置已保存', snapshot });
      return;
    }
    if (saveController.current && !saveController.current.signal.aborted) return;
    const controller = new AbortController();
    saveController.current = controller;
    try {
      setSaving(true);
      setSaveStatus(null);
      const payload = { settings, selectedCoursesByAccount: { [username]: selectedCourses } };
      const response = await api.post('/config', payload, { signal: controller.signal });
      if (controller.signal.aborted) return;
      if (!response.data.status) {
        setSaveStatus({ type: 'error', message: response.data.msg || '保存失败，请重试' });
      } else {
        setSavedSnapshot(snapshot);
        setSaveStatus({ type: 'success', message: '配置已保存', snapshot });
      }
    } catch (err) {
      if (!controller.signal.aborted) setSaveStatus({ type: 'error', message: '保存请求失败，请重试' });
    } finally {
      if (!controller.signal.aborted) setSaving(false);
      if (saveController.current === controller) saveController.current = null;
    }
  };

  /* ---------- 载入态 ---------- */
  if (loading || loadedAccount !== username) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4">
        <Loader2 className="h-7 w-7 animate-spin text-brand" aria-hidden="true" />
        <p className="text-sm text-faint">正在获取课程列表</p>
        {activeTaskId && <Button variant="outline" onClick={onReturnToTask} disabled={loggingOut}>返回任务进度</Button>}
      </div>
    );
  }

  /* ---------- 主视图 ---------- */
  return (
    <div className="course-page min-h-screen bg-canvas">
      {/* 顶栏 */}
      <header className="sticky top-0 z-20 border-b border-line bg-white/85 backdrop-blur">
        <div className="page-shell flex h-16 items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand">
              <GraduationCap className="h-5 w-5 text-white" aria-hidden="true" />
            </div>
            <div>
              <p className="text-[15px] font-semibold leading-tight">超星学习通</p>
              <p className="text-xs leading-tight text-faint">课程管理</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <RepositoryLink />
            {userInfo?.username && (
              <span className="hidden font-mono text-xs text-faint sm:inline tnum">
                {userInfo.username}
              </span>
            )}
            <Button variant="ghost" size="sm" onClick={onLogout} disabled={loggingOut}>
              <LogOut className="h-3.5 w-3.5" aria-hidden="true" />
              {loggingOut ? '退出中' : '退出登录'}
            </Button>
          </div>
        </div>
      </header>

      <main className="course-main page-shell py-5">
        {/* 页首 */}
        <div className="course-heading mb-5 shrink-0 animate-stagger-up">
          <h1 className="text-2xl font-semibold tracking-tight">选择课程并配置学习参数</h1>
          <p className="mt-1.5 text-sm text-faint">
            {selectionNotice || '请至少选择一门课程；未选课程时无法开始学习'}
          </p>
        </div>

        {activeTaskId && (
          <div className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-brand/20 bg-brand-soft/60 px-5 py-4">
            <p className="text-sm text-body">{taskRunning ? '已有任务运行中，可继续查看进度' : '上次任务已结束，可查看执行结果'}</p>
            <Button size="sm" variant="outline" onClick={onReturnToTask} disabled={loggingOut}>{taskRunning ? '返回运行任务' : '查看上次任务'}</Button>
          </div>
        )}
        {loadError && (
          <div role="alert" className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-xl bg-danger/5 px-5 py-4 text-sm text-danger">
            <span>{loadError}</span>
            <Button size="sm" variant="outline" onClick={() => setLoadVersion((value) => value + 1)}>重新加载</Button>
          </div>
        )}

        {editingTiku && <TikuConfigEditor settings={settings} onChange={setSettings} onBack={() => setEditingTiku(false)} />}
        <div hidden={editingTiku} className="course-body grid grid-cols-1 gap-5 md:grid-cols-[minmax(0,1fr)_clamp(19.5rem,32vw,30rem)] 2xl:gap-8">
          {/* 左:课程列表 */}
          <section
            aria-label="课程列表"
            className="course-list flex min-h-0 flex-col rounded-xl border border-line bg-white shadow-card animate-stagger-up"
            style={{ animationDelay: '80ms' }}
          >
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-4">
              <h2 className="flex items-center gap-2 text-[15px] font-semibold">
                <BookOpen className="h-4.5 w-4.5 text-brand" aria-hidden="true" />
                课程列表
                <span className="ml-1 rounded-full bg-soft px-2 py-0.5 text-xs text-faint tnum">
                  已选 {selectedCount} / 共 {courses.length}
                </span>
              </h2>
              <div className="flex flex-wrap items-center gap-2">
                <Button variant="ghost" size="sm" onClick={selectAllVisible} disabled={!canSelect || !filteredCourses.length} title="Ctrl+A / ⌘A 全选当前列表" aria-keyshortcuts="Control+A Meta+A">全选</Button>
                <Button variant="ghost" size="sm" onClick={() => setSelectedCourses([])} disabled={!canSelect || !selectedCount}>清空</Button>
                <div className="relative">
                  <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-faint" aria-hidden="true" />
                  <input
                    type="search"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="搜索课程"
                    aria-label="搜索课程"
                    className="h-8 w-44 rounded-lg border border-line bg-white pl-8 pr-3 text-[13px] placeholder:text-faint focus:border-brand focus:outline-none focus:ring-4 focus:ring-brand/15"
                  />
                </div>
              </div>
            </div>

            {courses.length === 0 ? (
              <div className="px-8 py-16 text-center">
                <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-full bg-soft">
                  <BookX className="h-5 w-5 text-faint" aria-hidden="true" />
                </div>
                <p className="mt-4 text-[15px] font-medium">暂无课程</p>
                <p className="mt-1 text-[13px] text-faint">
                  未能从学习通获取课程，请确认账号后再试
                </p>
              </div>
            ) : (
              <ul className="min-h-0 divide-y divide-line overflow-y-auto overscroll-contain scroll-brutal px-2 py-1.5">
                {filteredCourses.map((course) => {
                  const selected = selectedCourses.includes(course.courseId);
                  return (
                    <li key={course.courseId}>
                      <button
                        type="button"
                        onClick={() => toggleCourse(course.courseId)}
                        aria-pressed={selected}
                        disabled={!canSelect}
                        title={course.title}
                        className={`group flex w-full items-center gap-3.5 rounded-lg px-3 py-3 text-left transition-colors duration-150 hover:bg-soft focus-visible:bg-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand ${
                          selected ? 'bg-brand-soft/60' : ''
                        }`}
                      >
                        <span
                          className={`flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-[5px] border-[1.5px] transition-colors duration-150 ${
                            selected
                              ? 'border-brand bg-brand text-white'
                              : 'border-faint/40 text-transparent group-hover:border-faint'
                          }`}
                          aria-hidden="true"
                        >
                          {selected && <Check className="h-3 w-3" strokeWidth={3.5} />}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block break-words text-sm font-medium text-ink">
                            {course.title}
                          </span>
                          <span className="mt-0.5 block font-mono text-xs text-faint tnum">
                            ID: {course.courseId}
                          </span>
                        </span>
                      </button>
                    </li>
                  );
                })}
                {filteredCourses.length === 0 && (
                  <li className="py-10 text-center text-[13px] text-faint">
                    未找到与「{query}」匹配的课程
                  </li>
                )}
              </ul>
            )}
          </section>

          {/* 右:配置面板，配置区限高滚动，操作卡始终可见 */}
          <aside
            aria-label="学习配置"
            className="course-config flex min-h-0 flex-col gap-3 animate-stagger-up"
            style={{ animationDelay: '160ms' }}
          >
            <div className="course-parameters flex min-h-0 flex-1 flex-col rounded-xl border border-line bg-white shadow-card">
              <div className="flex shrink-0 items-center gap-2 border-b border-line px-5 py-3.5">
                <SlidersHorizontal className="h-4 w-4 text-brand" aria-hidden="true" />
                <h2 className="text-[15px] font-semibold">学习配置</h2>
              </div>

              <div className="min-h-0 overflow-y-auto overscroll-contain" tabIndex={0} aria-label="学习参数">
                <div className="p-5">
                  <CourseToolSettings
                    taskType={taskType} onTaskTypeChange={setTaskType}
                    options={toolOptions} onOptionsChange={setToolOptions}
                    disabled={starting || loggingOut || !!loadError}
                  />
                </div>

                {taskType === 'study' && <div className="space-y-5 border-t border-line p-5">
                  <div className="space-y-1.5">
                    <div className="flex items-center justify-between">
                      <Label htmlFor="speed">播放倍速</Label>
                      <span className="font-mono text-xs font-medium text-brand tnum">
                        {Number(settings.speed).toFixed(1)}x
                      </span>
                    </div>
                    <input
                      id="speed"
                      type="range"
                      min="1"
                      max="2"
                      step="0.1"
                      value={settings.speed}
                      onChange={(e) => setSettings({ ...settings, speed: parseFloat(e.target.value) })}
                      className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-line accent-brand focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-brand/15"
                    />
                    <p className="text-xs text-faint">范围 1.0–2.0</p>
                  </div>

                  <div className="space-y-1.5">
                    <Label htmlFor="jobs">并发章节数</Label>
                    <NumberInput
                      id="jobs"
                      min={1}
                      max={10}
                      step="1"
                      integer
                      value={settings.jobs}
                      onValueChange={(jobs) => setSettings({ ...settings, jobs })}
                      onValidityChange={(valid) => setFieldValid('jobs', valid)}
                      hint="同时处理的章节数量，默认 1，建议不超过 4"
                    />
                  </div>

                  <div className="space-y-1.5">
                    <Label htmlFor="notopen">未开放章节处理</Label>
                    <Select
                      id="notopen"
                      value={settings.notopen_action}
                      onChange={(e) => setSettings({ ...settings, notopen_action: e.target.value })}
                    >
                      <option value="retry">重试</option>
                      <option value="continue">跳过</option>
                    </Select>
                  </div>

                  <div className="border-t border-line pt-5">
                    <AdvancedSettings settings={settings} onChange={setSettings} onFieldValidity={setFieldValid}
                      onEditTiku={(trigger) => { editorTrigger.current = trigger; setEditingTiku(true); }} />
                  </div>
                </div>}
              </div>
            </div>

            <div className="course-actions shrink-0 space-y-2.5 rounded-xl border border-line bg-white p-4 shadow-card">
              <Button className="w-full" size="lg" onClick={handleStartStudy} disabled={!canStart}>
                {starting ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                    任务启动中
                  </>
                ) : (
                  <>
                    <Play className="h-4 w-4" aria-hidden="true" />
                    {startLabel}
                  </>
                )}
              </Button>
              <p className="text-center text-xs text-faint">{taskRunning ? '当前任务结束后可开始新任务' : selectedCount ? `将${taskType === 'study' ? '学习' : '处理'}已勾选的 ${selectedCount} 门课程` : '请至少勾选一门课程'}</p>
              {taskType === 'study' && <Button variant="outline" className="w-full" size="sm" onClick={handleSaveConfig} disabled={loggingOut || saving || !!loadError}>
                {saving ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                    保存中
                  </>
                ) : (
                  <>
                    <Save className="h-3.5 w-3.5" aria-hidden="true" />
                    保存当前配置
                  </>
                )}
              </Button>}
              {startError && (
                <div
                  role="alert"
                  className="flex items-start gap-2 rounded-lg bg-danger/5 px-3 py-2.5 text-xs leading-relaxed text-danger animate-fade-in"
                >
                  <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                  <span>{startError}</span>
                </div>
              )}
              {dirty && <p role="status" className="text-center text-xs text-warning-ink">有未保存的更改</p>}
              {saveStatus && (saveStatus.type === 'error' || saveStatus.snapshot === currentSnapshot) && (
                <p className={`text-center text-xs animate-fade-in ${saveStatus.type === 'error' ? 'text-danger' : 'text-success-ink'}`}
                  role={saveStatus.type === 'error' ? 'alert' : 'status'}>
                  {saveStatus.message}
                </p>
              )}
            </div>
          </aside>
        </div>
      </main>
    </div>
  );
};

export default CourseSelection;
