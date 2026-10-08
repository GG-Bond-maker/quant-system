import { useState } from 'react';
import type { ApiKeyCreated, ApiKeyItem, WebhookConfig } from '@/api/settings';
import { SectionCard } from '@/components/ui';

interface Props {
  webhook?: WebhookConfig;
  apiKeys: ApiKeyItem[];
  isAdmin: boolean;
  canResearch: boolean;
  onSaveWebhook: (wh: WebhookConfig) => Promise<void>;
  onTestWebhook: (wh: Partial<WebhookConfig>) => Promise<void>;
  onCreateApiKey: (payload: { name: string; permission: string; expires_days?: number | null }) => Promise<ApiKeyCreated>;
  onDeleteApiKey: (keyId: string) => Promise<void>;
  saving: boolean;
}

const EVENT_SUBSCRIPTIONS = [
  { key: 'backtest_done', label: '策略回测与参数寻优完成通知' },
  { key: 'risk_alert', label: '风控红线拦截与异动超限警报' },
  { key: 'sync_failed', label: '数据抓取同步异常与质量熔断' },
];

export function WebhookAndSecurityTab({
  webhook,
  apiKeys,
  isAdmin,
  canResearch,
  onSaveWebhook,
  onTestWebhook,
  onCreateApiKey,
  onDeleteApiKey,
  saving,
}: Props) {
  const [whForm, setWhForm] = useState<WebhookConfig>({
    enabled: webhook?.enabled ?? false,
    channel_type: webhook?.channel_type || 'feishu',
    webhook_url: webhook?.webhook_url || '',
    secret: webhook?.secret || '',
    events: webhook?.events || ['backtest_done', 'risk_alert', 'sync_failed'],
  });

  const [isWhDirty, setIsWhDirty] = useState(false);
  const [testingWh, setTestingWh] = useState(false);

  // API Key 弹窗
  const [createModal, setCreateModal] = useState(false);
  const [newKeyName, setNewKeyName] = useState('Jupyter Lab Client');
  const [newKeyPerm, setNewKeyPerm] = useState('read_only');
  const [newKeyExpires, setNewKeyExpires] = useState<number | ''>(30);
  const [createdResult, setCreatedResult] = useState<ApiKeyCreated | null>(null);
  const [copied, setCopied] = useState(false);

  const updateWh = <K extends keyof WebhookConfig>(k: K, v: WebhookConfig[K]) => {
    setWhForm((prev) => ({ ...prev, [k]: v }));
    setIsWhDirty(true);
  };

  const handleSaveWh = async () => {
    await onSaveWebhook(whForm);
    setIsWhDirty(false);
  };

  const handleTestWh = async () => {
    setTestingWh(true);
    try {
      await onTestWebhook(whForm);
    } finally {
      setTestingWh(false);
    }
  };

  const handleCreateKey = async () => {
    const res = await onCreateApiKey({
      name: newKeyName,
      permission: newKeyPerm,
      expires_days: newKeyExpires === '' ? null : Number(newKeyExpires),
    });
    setCreatedResult(res);
  };

  const handleCopyToken = () => {
    if (createdResult?.raw_token) {
      void navigator.clipboard.writeText(createdResult.raw_token);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const inputCls =
    'w-full rounded-md border border-hair bg-surface px-2.5 py-1.5 text-xs text-ink outline-none focus:border-brand-300 disabled:opacity-60';

  return (
    <div className="space-y-4">
      {/* 1. Webhook 通知信道 */}
      <SectionCard
        title="投研告警与通知 Webhook 机器人信道"
        action={
          <div className="flex items-center gap-2">
            {isWhDirty && <span className="text-2xs font-medium text-warn">● 未保存修改</span>}
            <button
              type="button"
              onClick={handleTestWh}
              disabled={!canResearch || testingWh || !whForm.webhook_url}
              className="rounded-md border border-hair bg-surface px-2.5 py-1 text-xs text-ink-secondary hover:bg-surface-alt disabled:opacity-50"
            >
              {testingWh ? '发送测试中…' : '🔔 发送测试消息'}
            </button>
            <button
              type="button"
              onClick={handleSaveWh}
              disabled={!isAdmin || !isWhDirty || saving}
              className="rounded-md bg-brand-500 px-3 py-1 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-50"
            >
              {saving ? '保存中…' : '保存 Webhook 配置'}
            </button>
          </div>
        }
      >
        <div className="space-y-4">
          <div className="flex items-center justify-between rounded-lg border border-hair bg-surface-alt p-3">
            <div>
              <div className="text-xs font-semibold text-ink">开启系统级机器人 Webhook 通知推送</div>
              <div className="text-3xs text-ink-muted">
                将盘后 AI 日报、回测结果与风控预警自动推送到指定的即时通讯群组
              </div>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={whForm.enabled}
              disabled={!isAdmin}
              onClick={() => updateWh('enabled', !whForm.enabled)}
              className={`relative h-5 w-9 rounded-full transition-colors ${
                whForm.enabled ? 'bg-brand-500' : 'bg-hair2'
              }`}
            >
              <span
                className={`absolute top-0.5 h-4 w-4 rounded-full bg-surface shadow transition-all ${
                  whForm.enabled ? 'left-[18px]' : 'left-0.5'
                }`}
              />
            </button>
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-ink-secondary">机器人协议类型</label>
              <select
                value={whForm.channel_type}
                disabled={!isAdmin}
                onChange={(e) => updateWh('channel_type', e.target.value as any)}
                className={inputCls}
              >
                <option value="feishu">飞书自定义机器人 (Feishu Webhook)</option>
                <option value="dingtalk">钉钉自定义机器人 (DingTalk Webhook)</option>
                <option value="wecom">企业微信群机器人 (WeCom Webhook)</option>
                <option value="custom">标准 HTTP POST (自定义 Webhook JSON)</option>
              </select>
            </div>

            <div className="sm:col-span-2">
              <label className="mb-1 block text-xs font-medium text-ink-secondary">Webhook 回调 URL</label>
              <input
                type="text"
                placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/xxxx"
                value={whForm.webhook_url}
                disabled={!isAdmin}
                onChange={(e) => updateWh('webhook_url', e.target.value)}
                className={inputCls}
              />
            </div>
          </div>

          <div>
            <label className="mb-1 block text-xs font-medium text-ink-secondary">安全加签 Secret (可选)</label>
            <input
              type="password"
              placeholder="留空则不开启加签校验"
              value={whForm.secret}
              disabled={!isAdmin}
              onChange={(e) => updateWh('secret', e.target.value)}
              className={inputCls}
            />
          </div>

          {/* 事件订阅列表 */}
          <div className="border-t border-hair pt-3">
            <div className="mb-2 text-xs font-medium text-ink-secondary">订阅推送信道事件</div>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
              {EVENT_SUBSCRIPTIONS.map((ev) => {
                const checked = whForm.events.includes(ev.key);
                return (
                  <label
                    key={ev.key}
                    className="flex cursor-pointer items-center gap-2 rounded border border-hair bg-surface-alt p-2 text-xs text-ink hover:bg-surface-sunken/40"
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={!isAdmin}
                      onChange={(e) => {
                        const next = e.target.checked
                          ? [...whForm.events, ev.key]
                          : whForm.events.filter((x) => x !== ev.key);
                        updateWh('events', next);
                      }}
                      className="h-3.5 w-3.5 accent-brand-500"
                    />
                    <span className="truncate">{ev.label}</span>
                  </label>
                );
              })}
            </div>
          </div>
        </div>
      </SectionCard>

      {/* 2. 开发者 API 密钥管理 */}
      <SectionCard
        title="开发者 API 密钥与个人访问令牌 (Personal Access Tokens)"
        action={
          <div className="flex items-center gap-2">
            {canResearch && (
              <button
                type="button"
                onClick={() => {
                  setCreatedResult(null);
                  setCreateModal(true);
                }}
                className="rounded-md bg-brand-500 px-3 py-1 text-xs font-medium text-white hover:bg-brand-600"
              >
                + 生成新 API 密钥
              </button>
            )}
          </div>
        }
      >
        <div className="space-y-3">
          <p className="text-2xs text-ink-muted leading-relaxed">
            API 密钥用于通过 Python SDK（`import aqp`）、Jupyter Notebook 或离线自动化脚本直接调用本平台全部回测、行情与多因子计算接口。
          </p>

          <div className="overflow-x-auto rounded-lg border border-hair">
            <table className="w-full text-left text-xs">
              <thead className="border-b border-hair bg-surface-alt text-2xs text-ink-muted">
                <tr>
                  <th className="px-3 py-2 font-medium">应用 / 标识名称</th>
                  <th className="px-3 py-2 font-medium">令牌掩码</th>
                  <th className="px-3 py-2 font-medium">权限范围</th>
                  <th className="px-3 py-2 font-medium">创建时间</th>
                  <th className="px-3 py-2 font-medium">过期时间</th>
                  <th className="px-3 py-2 text-right font-medium">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-hair bg-surface">
                {apiKeys.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="py-6 text-center text-xs text-ink-muted">
                      暂无活跃的开发者 API 密钥，点击右上角生成一个以供 Python 脚本连接
                    </td>
                  </tr>
                ) : (
                  apiKeys.map((item) => (
                    <tr key={item.key_id} className="hover:bg-surface-alt/50">
                      <td className="px-3 py-2 font-medium text-ink">{item.name}</td>
                      <td className="px-3 py-2 font-mono text-2xs text-ink-secondary">{item.prefix}</td>
                      <td className="px-3 py-2">
                        <span
                          className={`rounded px-1.5 py-0.5 text-3xs font-medium ${
                            item.permission === 'full_access'
                              ? 'bg-warn-bg text-warn'
                              : 'bg-brand-500/10 text-brand-600 dark:text-brand-400'
                          }`}
                        >
                          {item.permission === 'full_access' ? '完整读写与执行' : '只读分析'}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-2xs text-ink-muted">{item.created_at}</td>
                      <td className="px-3 py-2 text-2xs text-ink-muted">{item.expires_at || '永久有效'}</td>
                      <td className="px-3 py-2 text-right">
                        {canResearch && (
                          <button
                            type="button"
                            onClick={() => void onDeleteApiKey(item.key_id)}
                            className="rounded px-2 py-0.5 text-2xs text-danger hover:bg-danger-bg"
                          >
                            撤销
                          </button>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </SectionCard>

      {/* 生成新 Key 模态框 */}
      {createModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-ink/40" onClick={() => setCreateModal(false)} />
          <div className="relative z-10 w-full max-w-md rounded-lg border border-hair bg-surface p-5 shadow-xl">
            <h3 className="text-sm font-semibold text-ink">生成开发者访问令牌 (API Key)</h3>

            {createdResult ? (
              <div className="mt-4 space-y-3">
                <div className="rounded-md border border-success/30 bg-success-bg p-3 text-xs text-success">
                  <div className="font-semibold">密钥已成功生成！</div>
                  <div className="mt-1 text-2xs">
                    ⚠️ 安全提示：完整的 API 密钥**仅在此展示一次**，关闭弹窗后无法再次查看。请立即复制并妥善保管。
                  </div>
                </div>

                <div className="rounded-lg border border-hair bg-surface-alt p-3">
                  <div className="text-2xs text-ink-muted">Token 明文:</div>
                  <div className="mt-1 break-all font-mono text-xs font-semibold text-ink select-all">
                    {createdResult.raw_token}
                  </div>
                </div>

                <div className="flex justify-end gap-2 pt-2">
                  <button
                    type="button"
                    onClick={handleCopyToken}
                    className="rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600"
                  >
                    {copied ? '已复制到剪贴板！' : '一键复制 Token'}
                  </button>
                  <button
                    type="button"
                    onClick={() => setCreateModal(false)}
                    className="rounded-md border border-hair px-3 py-1.5 text-xs text-ink-secondary hover:bg-surface-alt"
                  >
                    关闭
                  </button>
                </div>
              </div>
            ) : (
              <div className="mt-4 space-y-3">
                <label className="block">
                  <span className="mb-1 block text-xs text-ink-secondary">令牌名称 / 用途说明</span>
                  <input
                    type="text"
                    value={newKeyName}
                    onChange={(e) => setNewKeyName(e.target.value)}
                    className={inputCls}
                    placeholder="如：生产环境定时推演、Jupyter 交互分析"
                  />
                </label>

                <label className="block">
                  <span className="mb-1 block text-xs text-ink-secondary">权限范围</span>
                  <select
                    value={newKeyPerm}
                    onChange={(e) => setNewKeyPerm(e.target.value)}
                    className={inputCls}
                  >
                    <option value="read_only">只读访问 (Read-Only · 获取行情/报表/因子)</option>
                    <option value="full_access">完整权限 (Full-Access · 可发起回测与交易推演)</option>
                  </select>
                </label>

                <label className="block">
                  <span className="mb-1 block text-xs text-ink-secondary">有效期</span>
                  <select
                    value={newKeyExpires}
                    onChange={(e) => setNewKeyExpires(e.target.value === '' ? '' : Number(e.target.value))}
                    className={inputCls}
                  >
                    <option value={7}>7 天有效 (临时联调)</option>
                    <option value={30}>30 天有效 (推荐)</option>
                    <option value={90}>90 天有效</option>
                    <option value="">永久有效 (长期作业)</option>
                  </select>
                </label>

                <div className="mt-4 flex justify-end gap-2 pt-2">
                  <button
                    type="button"
                    onClick={() => setCreateModal(false)}
                    className="rounded-md border border-hair px-3 py-1.5 text-xs text-ink-secondary hover:bg-surface-alt"
                  >
                    取消
                  </button>
                  <button
                    type="button"
                    onClick={() => void handleCreateKey()}
                    className="rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600"
                  >
                    立即生成
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
