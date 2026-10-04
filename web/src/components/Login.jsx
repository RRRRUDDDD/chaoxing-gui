import React, { useState, useEffect, useRef, useCallback } from 'react';
import Button from './ui/Button';
import Input from './ui/Input';
import Label from './ui/Label';
import { LogIn, Loader2, KeyRound, AlertCircle, Eye, EyeOff } from 'lucide-react';
import api from '../api/axios';
import { sessionStore } from '../lib/sessionStore';
import { loginLabels } from '../lib/uiText';

const Login = ({ onLoginSuccess }) => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const userRef = useRef(null);
  const requestRef = useRef(null);
  const successRef = useRef(onLoginSuccess);
  successRef.current = onLoginSuccess;

  const doLogin = useCallback(async (u, p, { useCookies = false, signal }) => {
    setError('');
    setLoading(true);

    try {
      const response = await api.post('/login', {
        username: u,
        password: p,
        use_cookies: useCookies,
      }, { signal });
      if (signal.aborted) return;

      if (response.data.status) {
        setPassword('');
        let persistenceError = '';
        try { await sessionStore.rememberLogin(u); } catch {
          persistenceError = '账号记忆未能保存，刷新后可能需要重新登录';
        }
        if (!signal.aborted) await successRef.current({ username: u, password: '', use_cookies: true }, persistenceError);
      } else {
        setError(response.data.msg || '登录失败，请检查账号信息后重试');
      }
    } catch (err) {
      if (!signal.aborted) setError(useCookies ? '保存的登录会话不可用，请输入密码重新登录' : err.response?.data?.msg || '网络连接异常，请确认服务已启动后重试');
    } finally {
      if (!signal.aborted) setLoading(false);
    }
  }, []);

  const handleLogin = async (e) => {
    e.preventDefault();
    if (loading) return;
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    await doLogin(username.trim(), password, { signal: controller.signal });
  };

  // The desktop bridge persists across random backend ports; only cookies are reused.
  useEffect(() => {
    const controller = new AbortController();
    requestRef.current = controller;
    const restore = async () => {
      try {
        const saved = await sessionStore.read();
        if (controller.signal.aborted) return;
        if (saved.login) {
          setUsername(saved.login.username);
          await doLogin(saved.login.username, '', { useCookies: true, signal: controller.signal });
        }
      } catch {
        if (!controller.signal.aborted) setError('读取保存的账号失败，请手动登录');
      } finally {
        if (!controller.signal.aborted) { setLoading(false); userRef.current?.focus(); }
      }
    };
    restore();
    return () => { controller.abort(); requestRef.current?.abort(); };
  }, [doLogin]);

  const errId = 'login-error';

  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas px-[clamp(1rem,4vw,4rem)] py-[clamp(2.5rem,7vh,5rem)]">
      <div className="w-full max-w-[clamp(400px,32vw,520px)] animate-pop-in">
        {/* 品牌区 */}
        <div className="mb-8 flex flex-col items-center text-center">
          <img
            src="/logo.png"
            alt="超星学习通"
            className="mb-4 h-20 w-20"
          />
          <h1 className="text-xl font-semibold tracking-tight">超星学习通 · 自动化学习助手</h1>
          <p className="mt-1.5 text-sm text-faint">登录以继续</p>
        </div>

        {/* 登录卡片 */}
        <div className="rounded-2xl border border-line bg-white p-[clamp(1.5rem,2vw,2.25rem)] shadow-lift">
          <form onSubmit={handleLogin} aria-describedby={error ? errId : undefined} className="space-y-5">
            <div className="space-y-1.5">
              <Label htmlFor="username">{loginLabels.phone}</Label>
              <Input
                id="username"
                ref={userRef}
                type="text"
                inputMode="tel"
                autoComplete="username"
                placeholder="请输入手机号"
                value={username}
                disabled={loading}
                onChange={(e) => setUsername(e.target.value)}
                required
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="password">{loginLabels.password}</Label>
              <div className="relative">
                <Input
                  id="password"
                  type={showPassword ? 'text' : 'password'}
                  autoComplete="current-password"
                  placeholder="请输入密码"
                  value={password}
                  disabled={loading}
                  onChange={(e) => setPassword(e.target.value)}
                  className="pr-10"
                  required
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((shown) => !shown)}
                  disabled={loading}
                  aria-label={showPassword ? '隐藏密码' : '显示密码'}
                  aria-pressed={showPassword}
                  title={showPassword ? '隐藏密码' : '显示密码'}
                  className="absolute inset-y-0 right-0 flex w-10 items-center justify-center rounded-r-lg text-faint transition-colors hover:text-ink focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-brand/20 disabled:pointer-events-none disabled:opacity-45"
                >
                  {showPassword
                    ? <EyeOff className="h-4 w-4" aria-hidden="true" />
                    : <Eye className="h-4 w-4" aria-hidden="true" />}
                </button>
              </div>
            </div>

            {error && (
              <div
                id={errId}
                role="alert"
                className="flex items-start gap-2 rounded-lg bg-danger/5 px-3 py-2.5 text-[13px] leading-relaxed text-danger animate-fade-in"
              >
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                <span>{error}</span>
              </div>
            )}

            <Button type="submit" size="lg" className="w-full" disabled={loading}>
              {loading ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  登录中
                </>
              ) : (
                <>
                  <LogIn className="h-4 w-4" aria-hidden="true" />
                  {loginLabels.submit}
                </>
              )}
            </Button>
          </form>
        </div>

        <p className="mt-6 text-center text-xs leading-relaxed text-faint">
          <KeyRound className="mr-1 inline h-3 w-3 -translate-y-px" aria-hidden="true" />
          本机记忆账号并使用登录会话，密码不写入本地存储
        </p>
        <p className="mt-1.5 text-center text-xs leading-relaxed text-faint">
          本程序仅供学习和研究使用，请勿用于商业或非法用途。
          <br />
          使用本程序产生的一切后果由使用者自行承担，本程序不提供任何明示或暗示的适配性、安全性或合法性担保。
        </p>
      </div>
    </div>
  );
};

export default Login;
