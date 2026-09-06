import React, { useState, useRef, useEffect, useCallback } from 'react';
import Login from './components/Login';
import CourseSelection from './components/CourseSelection';
import StudyProgress from './components/StudyProgress';
import api from './api/axios';
import { sessionStore, validTaskId } from './lib/sessionStore';
import { isTerminalStatus, startTaskPolling } from './lib/taskPolling';

function App() {
  const previewMode = new URLSearchParams(window.location.search).get('preview');
  const isPreview = previewMode === 'courses' || previewMode === 'progress';
  const [step, setStep] = useState(isPreview ? previewMode : 'login');
  const [userInfo, setUserInfo] = useState(isPreview ? { username: '138****0000', password: '', use_cookies: true } : null);
  const [taskId, setTaskId] = useState(previewMode === 'progress' ? 'preview-task' : null);
  const [taskStatus, setTaskStatus] = useState(previewMode === 'progress' ? 'completed' : null);
  const [starting, setStarting] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const [startError, setStartError] = useState('');
  const [monitorError, setMonitorError] = useState('');
  const [taskNotice, setTaskNotice] = useState(null);
  const startingRef = useRef(false);
  const loggingOutRef = useRef(false);
  const startController = useRef(null);
  const sessionGeneration = useRef(0);
  const taskRunning = !!taskId && !isTerminalStatus(taskStatus) && taskStatus !== 'missing';
  const currentTaskNotice = taskNotice?.username === userInfo?.username && taskNotice?.taskId === taskId ? taskNotice.message : '';

  useEffect(() => () => {
    sessionGeneration.current += 1;
    startController.current?.abort();
  }, []);

  const handleLoginSuccess = useCallback(async (info, notice = '') => {
    if (loggingOutRef.current) return;
    const generation = ++sessionGeneration.current;
    startController.current?.abort();
    startController.current = null;
    startingRef.current = false;
    setStarting(false);
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
    setStep(restored ? 'progress' : 'courses');
  }, []);

  const handleTaskStatus = useCallback((status) => setTaskStatus(status.status), []);
  const handleTaskMissing = useCallback(() => {
    setTaskStatus('missing');
    setMonitorError('');
    sessionStore.rememberTask(null).catch(() => {});
  }, []);

  // Keep the task reachable and prevent a second start while browsing courses.
  useEffect(() => {
    if (isPreview || step !== 'courses' || !taskRunning) return undefined;
    return startTaskPolling({
      api, taskId, includeDetails: false, onStatus: handleTaskStatus,
      onMissing: handleTaskMissing, onError: setMonitorError,
    });
  }, [isPreview, step, taskId, taskRunning, handleTaskStatus, handleTaskMissing]);

  const adoptTask = async (id, username, isCurrent) => {
    if (!isCurrent()) return;
    if (!validTaskId(id)) throw new Error('服务返回的任务 ID 无效');
    setTaskId(id);
    setTaskStatus('running');
    setMonitorError('');
    setTaskNotice(null);
    setStep('progress');
    try { await sessionStore.rememberTask({ username, taskId: id }); } catch {
      if (isCurrent()) setTaskNotice({ username, taskId: id, message: '任务已启动，但恢复信息未能保存，请记下进度页中的任务 ID' });
    }
  };

  const handleStartStudy = async (settings) => {
    if (loggingOutRef.current || startingRef.current || !userInfo || taskRunning || !settings.course_list?.length) return;
    if (isPreview) {
      setTaskId('preview-task');
      setTaskStatus('completed');
      setStep('progress');
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
      const response = await api.post('/start', { ...settings, ...userInfo }, { signal: controller.signal });
      if (!isCurrent()) return;
      if (!response.data.status) throw new Error(response.data.msg || '启动学习任务失败，请检查配置后重试');
      await adoptTask(response.data.data.task_id, username, isCurrent);
    } catch (error) {
      if (!isCurrent()) return;
      const existing = error.response?.status === 409 && error.response.data?.data?.task_id;
      if (validTaskId(existing)) await adoptTask(existing, username, isCurrent);
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
    setStarting(false);
    setStartError('');
    try {
      await sessionStore.clear();
      if (generation !== sessionGeneration.current) return;
      setUserInfo(null);
      setTaskId(null);
      setTaskStatus(null);
      setTaskNotice(null);
      setMonitorError('');
      setStep('login');
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
      {step === 'courses' && (
        <CourseSelection
          key={userInfo.username}
          userInfo={userInfo}
          onStartStudy={handleStartStudy}
          onLogout={handleLogout}
          starting={starting}
          loggingOut={loggingOut}
          startError={startError || monitorError || currentTaskNotice}
          activeTaskId={taskId}
          taskRunning={taskRunning}
          onReturnToTask={() => { if (!loggingOutRef.current) setStep('progress'); }}
          preview={isPreview}
        />
      )}
      {step === 'progress' && taskId && (
        <StudyProgress
          taskId={taskId}
          notice={currentTaskNotice}
          onBack={() => setStep('courses')}
          onStatus={handleTaskStatus}
          onMissing={handleTaskMissing}
          preview={isPreview}
        />
      )}
    </div>
  );
}

export default App;
