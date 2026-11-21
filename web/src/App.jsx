import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import Login from './components/Login';
import CourseSelection from './components/CourseSelection';
import StudyProgress from './components/StudyProgress';
import { ScheduledShutdownBanner } from './components/ShutdownBanner';
import api from './api/axios';
import { sessionStore, validTaskId } from './lib/sessionStore';
import { isTerminalStatus, startTaskPolling } from './lib/taskPolling';
import { expiredTaskMessage } from './lib/uiText';

function App() {
  // 预览模式由初始 URL 决定且整个会话不变：只解析一次，避免每次渲染重建 URLSearchParams。
  const previewMode = useMemo(() => new URLSearchParams(window.location.search).get('preview'), []);
  const isPreview = previewMode === 'courses' || previewMode === 'progress';
  const initialStep = isPreview ? previewMode : 'login';
  const [step, setStep] = useState(initialStep);
  const [userInfo, setUserInfo] = useState(isPreview ? { username: '138****0000', password: '', use_cookies: true } : null);
  const [taskId, setTaskId] = useState(previewMode === 'progress' ? 'preview-task' : null);
  const [taskStatus, setTaskStatus] = useState(previewMode === 'progress' ? 'completed' : null);
  const [shutdownInfo, setShutdownInfo] = useState(null);
  const [cancellingShutdown, setCancellingShutdown] = useState(false);
  const [cancelShutdownError, setCancelShutdownError] = useState('');
  const [starting, setStarting] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const [startError, setStartError] = useState('');
  const [monitorError, setMonitorError] = useState('');
  const [taskNotice, setTaskNotice] = useState(null);
  const [recovery, setRecovery] = useState(null);
  // 选课页的草稿只保留在当前会话：去过一次后保持挂载，往返进度页不丢失、也不写盘。
  const [coursesVisited, setCoursesVisited] = useState(initialStep === 'courses');
  const startingRef = useRef(false);
  const loggingOutRef = useRef(false);
  const startController = useRef(null);
  const recoveryController = useRef(null);
  const stopController = useRef(null);
  const cancelShutdownController = useRef(null);
  const shutdownOnCompleteRef = useRef(false);
  const sessionGeneration = useRef(0);
  const goToStep = useCallback((nextStep) => {
    if (nextStep === 'courses') setCoursesVisited(true);
    setStep(nextStep);
  }, []);
  const handleShutdownChange = useCallback((enabled) => {
    shutdownOnCompleteRef.current = enabled === true;
  }, []);
  const taskRunning = !!taskId && !isTerminalStatus(taskStatus) && taskStatus !== 'missing';
  const currentTaskNotice = taskNotice?.username === userInfo?.username && taskNotice?.taskId === taskId ? taskNotice.message : '';
  const currentRecovery = recovery?.username === userInfo?.username && recovery?.taskId === taskId ? recovery : null;

  useEffect(() => () => {
    sessionGeneration.current += 1;
    startController.current?.abort();
    recoveryController.current?.abort();
    stopController.current?.abort();
    cancelShutdownController.current?.abort();
  }, []);

  const recoverTask = useCallback(async (id, info) => {
    if (loggingOutRef.current || !validTaskId(id)) return;
    recoveryController.current?.abort();
    const controller = new AbortController();
    const generation = sessionGeneration.current;
    recoveryController.current = controller;
    const isCurrent = () => !loggingOutRef.current && generation === sessionGeneration.current
      && !controller.signal.aborted && recoveryController.current === controller;
    setRecovery({ username: info.username, taskId: id, pending: true, error: '' });
    try {
      const response = await api.post(`/task/${encodeURIComponent(id)}/resume`, info, { signal: controller.signal });
      if (!isCurrent()) return;
      if (!response.data.status) throw new Error(response.data.msg || '恢复任务失败');
      if (response.data.data?.task_id !== id) throw new Error('服务返回的任务 ID 无效');
      if (response.data.data.status !== 'running' && !isTerminalStatus(response.data.data.status)) throw new Error('任务尚未恢复，请重试');
      setTaskStatus(response.data.data.status);
      setRecovery(null);
    } catch (error) {
      if (!isCurrent()) return;
      if (error.response?.status === 404) {
        setTaskId(null);
        setTaskStatus(null);
        setShutdownInfo(null);
        setCancelShutdownError('');
        setTaskNotice(null);
        setRecovery(null);
        goToStep('courses');
        setStartError(expiredTaskMessage);
        try { await sessionStore.rememberTask(null); }
        catch {
          if (isCurrent()) setStartError('上次任务无法恢复，且清除旧记录失败；可重新选择课程开始学习。');
        }
      } else {
        const message = error.response?.data?.msg || error.message || '无法连接学习服务，请稍后重试';
        setRecovery({ username: info.username, taskId: id, pending: false, error: `恢复任务失败：${message}` });
      }
    } finally {
      if (isCurrent()) recoveryController.current = null;
    }
  }, [goToStep]);

  const handleLoginSuccess = useCallback(async (info, notice = '') => {
    if (loggingOutRef.current) return;
    const generation = ++sessionGeneration.current;
    startController.current?.abort();
    recoveryController.current?.abort();
    stopController.current?.abort();
    cancelShutdownController.current?.abort();
    recoveryController.current = null;
    startController.current = null;
    stopController.current = null;
    cancelShutdownController.current = null;
    shutdownOnCompleteRef.current = false;
    startingRef.current = false;
    setStarting(false);
    setCancellingShutdown(false);
    setCancelShutdownError('');
    setShutdownInfo(null);
    let saved;
    try { saved = await sessionStore.read(); } catch { /* Login can continue without storage. */ }
    if (loggingOutRef.current || generation !== sessionGeneration.current) return;
    const restored = saved?.activeTask?.username === info.username ? saved.activeTask.taskId : null;
    setUserInfo(info);
    setTaskId(restored);
    setTaskStatus(restored ? 'checking' : null);
    setStartError(notice);
    setMonitorError('');
    setTaskNotice(null);
    setRecovery(restored ? { username: info.username, taskId: restored, pending: true, error: '' } : null);
    goToStep(restored ? 'progress' : 'courses');
    if (restored) void recoverTask(restored, info);
  }, [recoverTask, goToStep]);

  const handleTaskStatus = useCallback((status) => {
    setTaskStatus(status.status);
    setShutdownInfo(status.shutdown || null);
    if (status.shutdown?.state !== 'scheduled') setCancelShutdownError('');
    if (status.status === 'interrupted' && taskId && userInfo && !recoveryController.current) {
      void recoverTask(taskId, userInfo);
    }
  }, [taskId, userInfo, recoverTask]);
  const handleTaskMissing = useCallback(() => {
    setTaskStatus('missing');
    setShutdownInfo(null);
    setCancelShutdownError('');
    setMonitorError('');
    sessionStore.rememberTask(null).catch(() => {});
  }, []);

  const handleCancelScheduledShutdown = useCallback(async () => {
    if (isPreview || loggingOutRef.current || cancellingShutdown || !taskId || !userInfo?.username) return;
    cancelShutdownController.current?.abort();
    const controller = new AbortController();
    const generation = sessionGeneration.current;
    cancelShutdownController.current = controller;
    setCancellingShutdown(true);
    setCancelShutdownError('');
    const isCurrent = () => !loggingOutRef.current && generation === sessionGeneration.current
      && !controller.signal.aborted && cancelShutdownController.current === controller;
    try {
      const response = await api.post(
        `/task/${encodeURIComponent(taskId)}/cancel-shutdown`,
        { username: userInfo.username },
        { signal: controller.signal },
      );
      if (!isCurrent()) return;
      if (!response.data.status) throw new Error(response.data.msg || '取消关机失败，请重试');
      setShutdownInfo({ state: response.data.data?.state || 'cancelled' });
    } catch (error) {
      if (!isCurrent()) return;
      if (error?.response?.status === 404) {
        handleTaskMissing();
        return;
      }
      setCancelShutdownError(error?.response?.data?.msg || error?.message || '取消关机失败，可在命令提示符运行 shutdown /a 手动取消');
    } finally {
      if (isCurrent()) {
        cancelShutdownController.current = null;
        setCancellingShutdown(false);
      }
    }
  }, [isPreview, cancellingShutdown, taskId, userInfo, handleTaskMissing]);

  // Request a stop and let polling publish the resulting terminal state.
  const handleStopTask = useCallback(async () => {
    if (!taskId || !userInfo || loggingOutRef.current) return;
    const controller = new AbortController();
    const generation = sessionGeneration.current;
    stopController.current?.abort();
    stopController.current = controller;
    const isCurrent = () => !loggingOutRef.current && generation === sessionGeneration.current
      && !controller.signal.aborted && stopController.current === controller;
    try {
      const response = await api.post(`/task/${encodeURIComponent(taskId)}/stop`,
        { username: userInfo.username }, { signal: controller.signal });
      if (!isCurrent()) return;
      if (!response.data.status) throw new Error(response.data.msg || '停止任务失败，请重试');
      // An interrupted task is stopped outright, so reflect it without waiting.
      if (response.data.data?.state === 'cancelled') setTaskStatus('cancelled');
    } catch (error) {
      if (!isCurrent()) return;
      if (error.response?.status === 404) {
        handleTaskMissing();
        return;
      }
      throw error;
    } finally {
      if (isCurrent()) stopController.current = null;
    }
  }, [taskId, userInfo, handleTaskMissing]);

  // Keep the task reachable and prevent a second start while browsing courses.
  useEffect(() => {
    if (isPreview || step !== 'courses' || !taskRunning || currentRecovery) return undefined;
    return startTaskPolling({
      api, taskId, includeDetails: false, onStatus: handleTaskStatus,
      onMissing: handleTaskMissing, onError: setMonitorError,
    });
  }, [isPreview, step, taskId, taskRunning, currentRecovery, handleTaskStatus, handleTaskMissing]);

  const adoptTask = async (id, username, isCurrent, resume = false) => {
    if (!isCurrent()) return;
    if (!validTaskId(id)) throw new Error('服务返回的任务 ID 无效');
    stopController.current?.abort();
    stopController.current = null;
    cancelShutdownController.current?.abort();
    cancelShutdownController.current = null;
    setTaskId(id);
    setTaskStatus('running');
    setShutdownInfo(null);
    setCancellingShutdown(false);
    setCancelShutdownError('');
    setMonitorError('');
    setTaskNotice(null);
    setRecovery(resume ? { username, taskId: id, pending: true, error: '' } : null);
    goToStep('progress');
    try { await sessionStore.rememberTask({ username, taskId: id }); } catch {
      if (isCurrent()) setTaskNotice({ username, taskId: id, message: '任务已启动，但恢复信息未能保存，请记下进度页中的任务 ID' });
    }
    if (resume && isCurrent()) await recoverTask(id, userInfo);
  };

  const handleStartStudy = async (settings) => {
    const hasReadUrls = settings.task_type === 'read_time' && settings.tool_options?.urls?.length > 0;
    if (loggingOutRef.current || startingRef.current || !userInfo || taskRunning || (!settings.course_list?.length && !hasReadUrls)) return;
    if (isPreview) {
      setTaskId('preview-task');
      setTaskStatus('completed');
      goToStep('progress');
      return;
    }
    startingRef.current = true;
    setStarting(true);
    setStartError('');
    const controller = new AbortController();
    const generation = sessionGeneration.current;
    const username = userInfo.username;
    startController.current = controller;
    const isCurrent = () => !loggingOutRef.current && generation === sessionGeneration.current
      && !controller.signal.aborted && startController.current === controller;
    try {
      const attachShutdown = settings.task_type
        && settings.task_type !== 'catalog'
        && settings.shutdown_on_complete === undefined
        && shutdownOnCompleteRef.current;
      const payload = attachShutdown
        ? { ...settings, shutdown_on_complete: true, ...userInfo }
        : { ...settings, ...userInfo };
      const response = await api.post('/start', payload, { signal: controller.signal });
      if (!isCurrent()) return;
      if (!response.data.status) throw new Error(response.data.msg || '启动学习任务失败，请检查配置后重试');
      await adoptTask(response.data.data.task_id, username, isCurrent);
    } catch (error) {
      if (!isCurrent()) return;
      const existing = error.response?.status === 409 && error.response.data?.data?.task_id;
      if (validTaskId(existing)) await adoptTask(existing, username, isCurrent, true);
      else setStartError(error.response?.data?.msg || error.message || '启动学习任务失败，请检查配置后重试');
    } finally {
      if (isCurrent()) {
        startController.current = null;
        startingRef.current = false;
        setStarting(false);
      }
    }
  };

  const handleLogout = async () => {
    if (loggingOutRef.current) return;
    // Block starts and login handoffs before asynchronous desktop storage clears.
    loggingOutRef.current = true;
    const generation = ++sessionGeneration.current;
    setLoggingOut(true);
    startController.current?.abort();
    recoveryController.current?.abort();
    stopController.current?.abort();
    cancelShutdownController.current?.abort();
    recoveryController.current = null;
    stopController.current = null;
    cancelShutdownController.current = null;
    setStarting(false);
    setCancellingShutdown(false);
    setCancelShutdownError('');
    setRecovery((previous) => previous?.pending ? { ...previous, pending: false, error: '恢复已中断，请重试恢复任务' } : previous);
    setStartError('');
    try {
      await sessionStore.clear();
      if (generation !== sessionGeneration.current) return;
      shutdownOnCompleteRef.current = false;
      setUserInfo(null);
      setTaskId(null);
      setTaskStatus(null);
      setShutdownInfo(null);
      setTaskNotice(null);
      setRecovery(null);
      setMonitorError('');
      goToStep('login');
    } catch {
      if (generation === sessionGeneration.current) setStartError('清除保存的账号失败，请重试退出登录');
    } finally {
      if (generation === sessionGeneration.current) {
        startController.current?.abort();
        startController.current = null;
        startingRef.current = false;
        setStarting(false);
        loggingOutRef.current = false;
        setLoggingOut(false);
      }
    }
  };

  return (
    <div className="App">
      {step === 'login' && <Login onLoginSuccess={handleLoginSuccess} />}
      {(step === 'courses' || (coursesVisited && userInfo)) && (
        <div hidden={step !== 'courses'}>
          <CourseSelection
            key={userInfo?.username}
            active={step === 'courses'}
            userInfo={userInfo}
            onStartStudy={handleStartStudy}
            onLogout={handleLogout}
            starting={starting}
            loggingOut={loggingOut}
            startError={startError || currentRecovery?.error || monitorError}
            notice={currentTaskNotice}
            activeTaskId={taskId}
            taskRunning={taskRunning}
            onReturnToTask={() => { if (!loggingOutRef.current) goToStep('progress'); }}
            onShutdownChange={handleShutdownChange}
            shutdownBanner={shutdownInfo?.state === 'scheduled' && taskId ? (
              <ScheduledShutdownBanner
                shutdownInfo={shutdownInfo}
                cancelling={cancellingShutdown}
                cancelError={cancelShutdownError}
                disabled={loggingOut}
                onCancel={handleCancelScheduledShutdown}
                description="后台刷取任务已完成，如仍需使用电脑请立即点击右侧按钮取消关机。"
              />
            ) : null}
            preview={isPreview}
          />
        </div>
      )}
      {step === 'progress' && taskId && (
        <StudyProgress
          key={`${userInfo.username}:${taskId}`}
          taskId={taskId}
          username={userInfo.username}
          notice={currentTaskNotice}
          onStartStudy={handleStartStudy}
          starting={starting}
          startError={startError}
          actionsDisabled={loggingOut}
          onBack={() => goToStep('courses')}
          onStatus={handleTaskStatus}
          onMissing={handleTaskMissing}
          onStop={handleStopTask}
          recovering={currentRecovery?.pending === true}
          recoveryError={currentRecovery?.error || ''}
          onRetryRecovery={() => { if (userInfo && taskId) void recoverTask(taskId, userInfo); }}
          preview={isPreview}
        />
      )}
    </div>
  );
}

export default App;
