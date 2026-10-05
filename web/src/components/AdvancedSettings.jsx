import { useEffect, useState } from 'react';
import Input from './ui/Input';
import Label from './ui/Label';
import Select from './ui/Select';
import NumberInput from './ui/NumberInput';
import SettingsDisclosure from './SettingsDisclosure';
import ExternalLink from './ExternalLink';
import { updateTikuSettings } from '../lib/courseSelection';
import { Database, Bell, AppWindow, Settings2 } from 'lucide-react';
import { desktopBridge, isTauriDesktop } from '../lib/desktopBridge';

const SectionHeader = ({ icon: Icon, title, description }) => (
  <div className="mb-4 flex items-start gap-2.5">
    <Icon className="mt-0.5 h-4 w-4 shrink-0 text-brand" aria-hidden="true" />
    <div className="min-w-0">
      <h3 className="text-sm font-semibold leading-tight">{title}</h3>
      <p className="mt-1 text-xs leading-snug text-faint">{description}</p>
    </div>
  </div>
);

// 折叠标题上的题库状态摘要：让用户不必展开也能知道当前配置形态。
export function tikuStatusSummary(tiku = {}) {
  const configured = typeof tiku.config === 'string' && tiku.config.trim() !== '';
  if (!configured) return { text: '未配置', cls: 'bg-warning/10 text-warning-ink' };
  return String(tiku.submit) === 'true'
    ? { text: '自动提交', cls: 'bg-brand-soft text-brand' }
    : { text: '仅保存答案', cls: 'bg-soft text-body' };
}

// Desktop-only: what the window close button does, stored by the host.
export const CloseActionSetting = () => {
  const [value, setValue] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    desktopBridge.readPreferences()
      .then((preferences) => { if (active) setValue(preferences.closeAction); })
      .catch(() => { if (active) setError('无法读取关闭方式'); });
    return () => { active = false; };
  }, []);

  const change = async (next) => {
    const previous = value;
    setValue(next);
    setError('');
    try {
      setValue((await desktopBridge.writePreferences(next)).closeAction);
    } catch {
      setValue(previous);
      setError('无法保存关闭方式，请重试');
    }
  };

  return (
    <section className="rounded-xl border border-line p-4">
      <SectionHeader icon={AppWindow} title="桌面窗口" description="点击窗口右上角关闭按钮时的操作" />
      <div className="space-y-1.5">
        <Label htmlFor="close-action">关闭窗口时</Label>
        <Select id="close-action" value={value} disabled={!value} onChange={(e) => void change(e.target.value)}>
          {!value && <option value="">读取中…</option>}
          <option value="ask">每次询问</option>
          <option value="minimize">最小化</option>
          <option value="tray">最小化到托盘</option>
          <option value="exit">直接退出</option>
        </Select>
        {error && <p role="alert" className="text-xs text-danger">{error}</p>}
      </div>
    </section>
  );
};

// 答题设置：仅自动学习使用，标题携带“未配置 / 仅保存答案 / 自动提交”状态。
export const TikuSettings = ({ settings, onChange, onFieldValidity }) => {
  const handleTikuChange = (field, value) => onChange(updateTikuSettings(settings, field, value));
  const summary = tikuStatusSummary(settings.tiku_config);

  return (
    <SettingsDisclosure title="答题设置" icon={Database} summary={summary}>
      <div className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="tiku-config">题库配置</Label>
          <textarea
            id="tiku-config"
            rows={10}
            spellCheck={false}
            placeholder={'[\n  {\n    "name": "示例题库",\n    "url": "https://example.com/search",\n    "method": "get",\n    "data": { "question": "${title}" },\n    "handler": "return (res)=> res.code === 1 ? [res.question, res.answer] : undefined"\n  }\n]'}
            value={settings.tiku_config?.config || ''}
            onChange={(e) => handleTikuChange('config', e.target.value)}
            className="flex w-full rounded-lg border border-line bg-white px-3 py-2 font-mono text-xs text-ink placeholder:text-faint hover:border-faint/50 focus:border-brand focus:outline-none focus:ring-4 focus:ring-brand/15"
          />
          <p className="text-xs leading-relaxed text-faint">
            留空则不自动答题；编辑时不会请求订阅链接，点击“保存为默认配置”后才会保存。格式与{' '}
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
        <div className="space-y-1.5">
          <label htmlFor="tiku-verify-ssl" className="flex items-center gap-2 text-[13px] font-medium text-body">
            <input
              id="tiku-verify-ssl"
              type="checkbox"
              className="h-4 w-4 accent-brand"
              checked={settings.tiku_config?.verify_ssl !== false}
              onChange={(e) => handleTikuChange('verify_ssl', e.target.checked)}
            />
            校验题库 HTTPS 证书
          </label>
          {settings.tiku_config?.verify_ssl === false && (
            <p role="alert" className="text-xs text-danger">
              已关闭证书校验：题库请求可能被中间人窃听或篡改，仅在确认题库地址可信时使用。
            </p>
          )}
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="tiku-submit">自动提交答题</Label>
          <Select id="tiku-submit" value={settings.tiku_config?.submit || 'false'} onChange={(e) => handleTikuChange('submit', e.target.value)}>
            <option value="false">仅保存，不提交</option>
            <option value="true">达到覆盖率后自动提交</option>
          </Select>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="tiku-cover-rate">最低覆盖率</Label>
            <NumberInput
              id="tiku-cover-rate"
              onValidityChange={(valid) => onFieldValidity?.('tiku-cover-rate', valid)}
              min={0}
              max={1}
              step="0.1"
              value={settings.tiku_config?.cover_rate ?? 0.9}
              onValueChange={(value) => handleTikuChange('cover_rate', value)}
              hint="0.0–1.0，推荐 0.9"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="tiku-delay">查询延迟（秒）</Label>
            <NumberInput
              id="tiku-delay"
              onValidityChange={(valid) => onFieldValidity?.('tiku-delay', valid)}
              min={0}
              step="0.5"
              value={settings.tiku_config?.delay ?? 1.0}
              onValueChange={(value) => handleTikuChange('delay', value)}
              hint="题库查询间隔"
            />
          </div>
        </div>
      </div>
    </SettingsDisclosure>
  );
};

// 应用设置：通知与窗口关闭行为，与本次执行的功能无关，任何任务类型下都可配置。
export const AppSettings = ({ settings, onChange }) => {
  const handleNotificationChange = (field, value) => {
    onChange({
      ...settings,
      notification_config: {
        ...settings.notification_config,
        [field]: value,
      },
    });
  };

  return (
    <SettingsDisclosure title="应用设置" icon={Settings2}>
      <div className="space-y-5">
        <div>
          <SectionHeader icon={Bell} title="任务通知" description="任务完成或失败时的提醒方式" />
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="notification-provider">通知服务</Label>
              <Select
                id="notification-provider"
                value={settings.notification_config?.provider || ''}
                onChange={(e) => handleNotificationChange('provider', e.target.value)}
              >
                <option value="">不使用通知</option>
                <option value="Windows">Windows 系统通知</option>
                <option value="ServerChan">Server酱</option>
                <option value="Qmsg">Qmsg酱</option>
                <option value="Bark">Bark</option>
                <option value="Telegram">Telegram</option>
              </Select>
            </div>

            {settings.notification_config?.provider &&
              settings.notification_config?.provider !== 'Windows' && (
                <>
                  <div className="space-y-1.5">
                    <Label htmlFor="notification-url">通知 URL</Label>
                    <Input
                      id="notification-url"
                      type="text"
                      placeholder="https://..."
                      value={settings.notification_config?.url || ''}
                      onChange={(e) => handleNotificationChange('url', e.target.value)}
                    />
                    <p className="break-all font-mono text-xs text-faint">
                      {settings.notification_config?.provider === 'ServerChan' && 'https://sctapi.ftqq.com/YOUR_KEY.send'}
                      {settings.notification_config?.provider === 'Qmsg' && 'https://qmsg.zendee.cn/send/YOUR_KEY'}
                      {settings.notification_config?.provider === 'Bark' && 'https://api.day.app/YOUR_KEY/'}
                      {settings.notification_config?.provider === 'Telegram' && 'https://api.telegram.org/botYOUR_TOKEN/sendMessage'}
                    </p>
                  </div>

                  {settings.notification_config?.provider === 'Telegram' && (
                    <div className="space-y-1.5">
                      <Label htmlFor="tg-chat-id">Chat ID</Label>
                      <Input
                        id="tg-chat-id"
                        type="text"
                        placeholder="123456789"
                        value={settings.notification_config?.tg_chat_id || ''}
                        onChange={(e) => handleNotificationChange('tg_chat_id', e.target.value)}
                      />
                    </div>
                  )}
                </>
              )}
          </div>
        </div>

        {isTauriDesktop() && <CloseActionSetting />}
      </div>
    </SettingsDisclosure>
  );
};
