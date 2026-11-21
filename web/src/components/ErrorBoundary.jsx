import React from 'react';
import { AlertCircle, RefreshCw } from 'lucide-react';
import Button from './ui/Button';

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  handleReload = () => {
    window.location.reload();
  };

  handleReset = () => {
    this.setState({ error: null });
  };

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const message = error instanceof Error ? error.message : String(error || '发生未知错误');

    return (
      <main className="flex min-h-screen items-center justify-center bg-canvas px-[clamp(1rem,4vw,4rem)] py-12">
        <section
          role="alert"
          aria-live="assertive"
          className="w-full max-w-md rounded-2xl border border-line bg-white p-8 text-center shadow-lift"
        >
          <AlertCircle className="mx-auto mb-4 h-8 w-8 text-danger" aria-hidden="true" />
          <h1 className="text-lg font-semibold text-ink">界面遇到意外错误</h1>
          <p className="mt-2 text-sm leading-relaxed text-body">
            请刷新页面重新加载；后台正在运行的学习任务不会因此中断，刷新后可自动恢复查看。
          </p>
          {message && (
            <p className="mt-3 break-words rounded-lg bg-danger/5 px-3 py-2 text-left font-mono text-xs leading-relaxed text-danger">
              {message}
            </p>
          )}
          <div className="mt-6 flex flex-wrap items-center justify-center gap-2.5">
            <Button type="button" onClick={this.handleReload}>
              <RefreshCw className="h-4 w-4" aria-hidden="true" />
              刷新页面
            </Button>
            <Button type="button" variant="outline" onClick={this.handleReset}>
              尝试继续
            </Button>
          </div>
        </section>
      </main>
    );
  }
}

export default ErrorBoundary;
