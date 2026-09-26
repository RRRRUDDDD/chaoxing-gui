import React, { useState } from 'react';
import Input from './ui/Input';
import Label from './ui/Label';
import Select from './ui/Select';
import NumberInput from './ui/NumberInput';
import { Settings2, Database, Bell, Eye, ChevronDown } from 'lucide-react';

const SectionHeader = ({ icon: Icon, title, description }) => (
  <div className="mb-4 flex items-start gap-2.5">
    <Icon className="mt-0.5 h-4 w-4 shrink-0 text-brand" aria-hidden="true" />
    <div className="min-w-0">
      <h3 className="text-sm font-semibold leading-tight">{title}</h3>
      <p className="mt-1 text-xs leading-snug text-faint">{description}</p>
    </div>
  </div>
);

const AdvancedSettings = ({ settings, onChange, onFieldValidity }) => {
  const [showAdvanced, setShowAdvanced] = useState(false);

  const handleTikuChange = (field, value) => {
    onChange({
      ...settings,
      tiku_config: {
        ...settings.tiku_config,
        [field]: value,
      },
    });
  };

  const handleNotificationChange = (field, value) => {
    onChange({
      ...settings,
      notification_config: {
        ...settings.notification_config,
        [field]: value,
      },
    });
  };

  const handleOcrChange = (field, value) => {
    onChange({
      ...settings,
      ocr_config: {
        ...(settings.ocr_config || {}),
        [field]: value,
      },
    });
  };

  return (
    <div>
      <button
        type="button"
        onClick={() => setShowAdvanced(!showAdvanced)}
        aria-expanded={showAdvanced}
        className="flex w-full items-center justify-between rounded-lg text-[13px] font-medium text-body transition-colors hover:text-brand focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-brand/20"
      >
        <span className="flex items-center gap-2">
          <Settings2 className="h-4 w-4" aria-hidden="true" />
          {showAdvanced ? '收起高级配置' : '展开高级配置'}
        </span>
        <ChevronDown
          className={`h-4 w-4 transition-transform duration-300 ease-out ${
            showAdvanced ? 'rotate-180' : ''
          }`}
          aria-hidden="true"
        />
      </button>

      {/* grid-template-rows 0fr→1fr 折叠,纯 CSS 过渡 */}
      <div
        className="grid transition-[grid-template-rows] duration-400 ease-out"
        style={{ gridTemplateRows: showAdvanced ? '1fr' : '0fr' }}
      >
        <div className="overflow-hidden">
          <div className="space-y-6 pt-5">
            {/* 题库配置 */}
            <section className="rounded-xl border border-line p-4">
              <SectionHeader icon={Database} title="题库配置" description="章节检测自动答题（可选）" />
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="tiku-provider">题库提供商</Label>
                  <Select
                    id="tiku-provider"
                    value={settings.tiku_config?.provider || ''}
                    onChange={(e) => handleTikuChange('provider', e.target.value)}
                  >
                    <option value="">不使用题库</option>
                    <option value="TikuYanxi">言溪题库</option>
                    <option value="TikuLike">LIKE知识库</option>
                    <option value="TikuAdapter">TikuAdapter</option>
                    <option value="AI">AI大模型</option>
                    <option value="SiliconFlow">硅基流动AI</option>
                  </Select>
                </div>

                {settings.tiku_config?.provider && (
                  <>
                    <div className="space-y-1.5">
                      <Label htmlFor="tiku-tokens">Token</Label>
                      <Input
                        id="tiku-tokens"
                        type="text"
                        placeholder="多个 token 用英文逗号分隔"
                        value={settings.tiku_config?.tokens || ''}
                        onChange={(e) => handleTikuChange('tokens', e.target.value)}
                      />
                      <p className="text-xs text-faint">言溪题库或 LIKE 知识库的 Token</p>
                    </div>

                    <div className="space-y-1.5">
                      <Label htmlFor="tiku-submit">自动提交答题</Label>
                      <Select
                        id="tiku-submit"
                        value={settings.tiku_config?.submit || 'false'}
                        onChange={(e) => handleTikuChange('submit', e.target.value)}
                      >
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

                    {(settings.tiku_config?.provider === 'AI' || settings.tiku_config?.provider === 'SiliconFlow') && (
                      <div className="space-y-4 border-t border-line pt-4">
                        <p className="text-xs font-semibold text-brand">AI 配置</p>
                        <div className="space-y-1.5">
                          <Label htmlFor="ai-endpoint">API Endpoint</Label>
                          <Input
                            id="ai-endpoint"
                            type="text"
                            placeholder="https://api.example.com/v1"
                            value={settings.tiku_config?.endpoint || ''}
                            onChange={(e) => handleTikuChange('endpoint', e.target.value)}
                          />
                        </div>
                        <div className="space-y-1.5">
                          <Label htmlFor="ai-key">API Key</Label>
                          <Input
                            id="ai-key"
                            type="password"
                            placeholder="your-api-key"
                            value={settings.tiku_config?.key || ''}
                            onChange={(e) => handleTikuChange('key', e.target.value)}
                          />
                        </div>
                        <div className="space-y-1.5">
                          <Label htmlFor="ai-model">模型名称</Label>
                          <Input
                            id="ai-model"
                            type="text"
                            placeholder="gpt-3.5-turbo"
                            value={settings.tiku_config?.model || ''}
                            onChange={(e) => handleTikuChange('model', e.target.value)}
                          />
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                          <div className="space-y-1.5">
                            <Label htmlFor="ai-min-interval">最小间隔（秒）</Label>
                            <NumberInput
                              id="ai-min-interval"
                          onValidityChange={(valid) => onFieldValidity?.('ai-min-interval', valid)}
                              min={0}
                              step="0.1"
                              value={
                                typeof settings.tiku_config?.min_interval_seconds === 'number'
                                  ? settings.tiku_config.min_interval_seconds
                                  : 3
                              }
                              onValueChange={(value) => handleTikuChange('min_interval_seconds', value)}
                            />
                          </div>
                          <div className="space-y-1.5">
                            <Label htmlFor="ai-concurrency">单卷最大并发</Label>
                            <NumberInput
                              id="ai-concurrency"
                          onValidityChange={(valid) => onFieldValidity?.('ai-concurrency', valid)}
                              min={1}
                              max={10}
                              step="1"
                              integer
                              value={settings.tiku_config?.ai_concurrency ?? 3}
                              onValueChange={(value) => handleTikuChange('ai_concurrency', value)}
                            />
                          </div>
                        </div>
                      </div>
                    )}

                    {settings.tiku_config?.provider === 'TikuAdapter' && (
                      <div className="space-y-1.5">
                        <Label htmlFor="adapter-url">TikuAdapter URL</Label>
                        <Input
                          id="adapter-url"
                          type="text"
                          placeholder="http://localhost:8080"
                          value={settings.tiku_config?.url || ''}
                          onChange={(e) => handleTikuChange('url', e.target.value)}
                        />
                      </div>
                    )}
                  </>
                )}
              </div>
            </section>

            {/* 通知配置 */}
            <section className="rounded-xl border border-line p-4">
              <SectionHeader icon={Bell} title="外部通知" description="完成或出错时推送（可选）" />
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
            </section>

            {/* OCR 配置 */}
            <section className="rounded-xl border border-line p-4">
              <SectionHeader icon={Eye} title="图片 OCR" description="识别题目图片中的文字与公式（可选）" />
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="ocr-provider">OCR 提供商</Label>
                  <Select
                    id="ocr-provider"
                    value={settings.ocr_config?.provider || ''}
                    onChange={(e) => handleOcrChange('provider', e.target.value)}
                  >
                    <option value="">不使用图片 OCR</option>
                    <option value="openai">OpenAI (GPT-4o)</option>
                    <option value="claude">Claude 3 (Anthropic)</option>
                    <option value="qwen">通义千问 VL</option>
                    <option value="siliconflow">硅基流动（国内推荐）</option>
                    <option value="openai_compatible">OpenAI 兼容 API</option>
                  </Select>
                  <p className="text-xs text-faint">
                    用于识别题目中的图片（如数学公式），提升答题准确率
                  </p>
                </div>

                {settings.ocr_config?.provider && (
                  <>
                    <div className="space-y-1.5">
                      <Label htmlFor="ocr-key">API Key</Label>
                      <Input
                        id="ocr-key"
                        type="password"
                        placeholder="sk-..."
                        value={settings.ocr_config?.key || ''}
                        onChange={(e) => handleOcrChange('key', e.target.value)}
                      />
                      <p className="text-xs text-faint">
                        {settings.ocr_config?.provider === 'siliconflow' && '硅基流动 API Key，可在 siliconflow.cn 获取'}
                        {settings.ocr_config?.provider === 'openai' && 'OpenAI API Key'}
                        {settings.ocr_config?.provider === 'claude' && 'Anthropic API Key'}
                        {settings.ocr_config?.provider === 'qwen' && '阿里云 DashScope API Key'}
                        {settings.ocr_config?.provider === 'openai_compatible' && '兼容 API 的密钥'}
                      </p>
                    </div>

                    <div className="space-y-1.5">
                      <Label htmlFor="ocr-endpoint">API 端点（可选）</Label>
                      <Input
                        id="ocr-endpoint"
                        type="text"
                        placeholder={
                          settings.ocr_config?.provider === 'openai' ? 'https://api.openai.com/v1/chat/completions' :
                          settings.ocr_config?.provider === 'claude' ? 'https://api.anthropic.com/v1/messages' :
                          settings.ocr_config?.provider === 'qwen' ? 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions' :
                          settings.ocr_config?.provider === 'siliconflow' ? 'https://api.siliconflow.cn/v1/chat/completions' :
                          'https://your-api-endpoint/v1/chat/completions'
                        }
                        value={settings.ocr_config?.endpoint || ''}
                        onChange={(e) => handleOcrChange('endpoint', e.target.value)}
                      />
                      <p className="text-xs text-faint">留空使用默认端点，自定义端点需填写完整 URL</p>
                    </div>

                    <div className="space-y-1.5">
                      <Label htmlFor="ocr-model">模型名称（可选）</Label>
                      <Input
                        id="ocr-model"
                        type="text"
                        placeholder={
                          settings.ocr_config?.provider === 'openai' ? 'gpt-4o' :
                          settings.ocr_config?.provider === 'claude' ? 'claude-3-5-sonnet-20241022' :
                          settings.ocr_config?.provider === 'qwen' ? 'qwen-vl-plus' :
                          settings.ocr_config?.provider === 'siliconflow' ? 'Qwen/Qwen2-VL-72B-Instruct' :
                          'gpt-4o'
                        }
                        value={settings.ocr_config?.model || ''}
                        onChange={(e) => handleOcrChange('model', e.target.value)}
                      />
                      <p className="text-xs text-faint">留空使用默认模型</p>
                    </div>
                  </>
                )}
              </div>
            </section>
          </div>
        </div>
      </div>
    </div>
  );
};

export default AdvancedSettings;
