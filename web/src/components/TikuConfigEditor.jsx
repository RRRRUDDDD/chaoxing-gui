import React, { useEffect, useRef } from 'react';
import Button from './ui/Button';
import Label from './ui/Label';
import ExternalLink from './ExternalLink';
import { desktopBridge } from '../lib/desktopBridge';
import { updateTikuSettings } from '../lib/courseSelection';

const TikuConfigEditor = ({ settings, onChange, onBack }) => {
  const heading = useRef(null);
  useEffect(() => { heading.current?.focus(); }, []);
  const handleTikuChange = (field, value) => onChange(updateTikuSettings(settings, field, value));
  return (
    <section aria-label="题库宽屏编辑" className="min-h-0 overflow-y-auto rounded-xl border border-line bg-white p-5 shadow-card">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <h2 ref={heading} tabIndex={-1} className="text-lg font-semibold focus:outline-none">编辑题库配置</h2>
        <Button variant="outline" onClick={onBack}>返回配置面板</Button>
      </div>
      <p className="mb-4 text-sm text-faint">修改会保留在当前页面；返回后点击“保存当前配置”才会保存。编辑时不会请求订阅链接。</p>
      <div className="space-y-1.5">
        <Label htmlFor="tiku-config">题库配置</Label>
        <textarea
          id="tiku-config"
          rows={18}
          spellCheck={false}
          placeholder={'[\n  {\n    "name": "示例题库",\n    "url": "https://example.com/search",\n    "method": "get",\n    "data": { "question": "${title}" },\n    "handler": "return (res)=> res.code === 1 ? [res.question, res.answer] : undefined"\n  }\n]'}
          value={settings.tiku_config?.config || ''}
          onChange={(e) => handleTikuChange('config', e.target.value)}
          className="flex min-h-64 w-full rounded-lg border border-line bg-white px-3 py-2 font-mono text-xs text-ink placeholder:text-faint hover:border-faint/50 focus:border-brand focus:outline-none focus:ring-4 focus:ring-brand/15"
        />
        <p className="text-xs text-faint">
          留空则不自动答题。格式与{' '}
          <ExternalLink
            href="https://docs.ocsjs.com/docs/work"
            openDesktop={desktopBridge.openOcsDocs}
            title="在浏览器中打开 OCS 题库配置"
            className="text-brand underline-offset-2 hover:underline"
          >
            OCS 题库配置
          </ExternalLink>
          {' '}相同，可以是 JSON 数组，也可以是订阅链接。占位符为 {'${title}'}、{'${options}'}、{'${type}'}。
        </p>
      </div>

    </section>
  );
};
export default TikuConfigEditor;
