import { useState } from 'react';
import type { Preferences } from '@/api/settings';
import { SectionCard } from '@/components/ui';

interface Props {
  preferences: Preferences;
  userRole?: string;
  onSave: (prefs: Preferences) => Promise<void>;
  onThemeChange: (theme: Preferences['theme']) => void;
  saving: boolean;
}

function Segmented<T extends string>({
  value,
  onChange,
  options,
}: {
  value: T;
  onChange: (v: T) => void;
  options: Array<{ key: T; label: string }>;
}) {
  return (
    <div className="flex items-center gap-0.5 rounded-md border border-hair bg-surface-alt p-0.5">
      {options.map((o) => (
        <button
          key={o.key}
          type="button"
          onClick={() => onChange(o.key)}
          className={`flex-1 whitespace-nowrap rounded px-2.5 py-1 text-xs transition-colors ${
            value === o.key
              ? 'bg-brand-500 font-medium text-white shadow-sm'
              : 'text-ink-secondary hover:text-ink'
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={`relative h-5 w-9 rounded-full transition-colors ${checked ? 'bg-brand-500' : 'bg-hair2'}`}
    >
      <span
        className={`absolute top-0.5 h-4 w-4 rounded-full bg-surface shadow transition-all ${
          checked ? 'left-[18px]' : 'left-0.5'
        }`}
      />
    </button>
  );
}

export function PreferencesTab({
  preferences,
  userRole,
  onSave,
  onThemeChange,
  saving,
}: Props) {
  const [form, setForm] = useState<Preferences>(preferences);
  const [isDirty, setIsDirty] = useState(false);
  const [profileModal, setProfileModal] = useState(false);

  const update = <K extends keyof Preferences>(k: K, v: Preferences[K]) => {
    setForm((prev) => ({ ...prev, [k]: v }));
    setIsDirty(true);
    if (k === 'theme') {
      onThemeChange(v as Preferences['theme']);
    }
  };

  const handleSave = async () => {
    await onSave(form);
    setIsDirty(false);
  };

  const inputCls =
    'w-full rounded-md border border-hair bg-surface px-2.5 py-1.5 text-xs text-ink outline-none focus:border-brand-300';

  return (
    <div className="space-y-4">
      {/* 个人身份与平台偏好卡片 */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* 用户资料卡 */}
        <SectionCard title="投研账户与身份">
          <div className="flex flex-col justify-between h-full space-y-4">
            <div className="flex items-center gap-3">
              <div className="flex h-12 w-12 items-center justify-center rounded-full bg-brand-500/10 text-brand-600 dark:text-brand-400">
                <svg viewBox="0 0 24 24" className="h-6 w-6" fill="currentColor">
                  <path d="M12 12a4.5 4.5 0 1 0-4.5-4.5A4.5 4.5 0 0 0 12 12Zm0 2c-4 0-7.5 2-7.5 4.5V21h15v-2.5C19.5 16 16 14 12 14Z" />
                </svg>
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm font-semibold text-ink">{form.nickname}</span>
                  <span className="rounded bg-brand-500/10 px-1.5 py-0.5 text-3xs font-medium text-brand-600 dark:text-brand-400">
                    {userRole?.toUpperCase() ?? 'VIEWER'}
                  </span>
                </div>
                <div className="truncate text-xs text-ink-muted">{form.email || '未绑定邮箱'}</div>
              </div>
            </div>

            <div className="rounded-lg border border-hair bg-surface-alt p-2.5 text-2xs text-ink-secondary space-y-1">
              <div className="flex justify-between">
                <span>权限等级:</span>
                <span className="font-semibold text-ink">{userRole === 'admin' ? '全功能系统管理员' : userRole === 'researcher' ? '量化投研分析师' : '只读查看者'}</span>
              </div>
              <div className="flex justify-between">
                <span>会话状态:</span>
                <span className="text-success font-medium">● 活跃在线</span>
              </div>
            </div>

            <button
              type="button"
              onClick={() => setProfileModal(true)}
              className="w-full rounded-md border border-hair bg-surface py-1.5 text-xs font-medium text-ink hover:bg-surface-alt"
            >
              修改昵称与邮箱资料
            </button>
          </div>
        </SectionCard>

        {/* 界面视觉与语言设置 */}
        <SectionCard title="界面显示与本地化" className="lg:col-span-2">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label className="mb-1.5 block text-xs font-medium text-ink-secondary">平台主题外观</label>
              <Segmented
                value={form.theme}
                onChange={(v) => update('theme', v)}
                options={[
                  { key: 'light', label: '浅色明亮' },
                  { key: 'dark', label: '深色极客' },
                  { key: 'auto', label: '跟随系统' },
                ]}
              />
              <p className="mt-1 text-3xs text-ink-muted">实时切换即刻生效，自动同步根据操作系统暗色设置自适应</p>
            </div>

            <div>
              <label className="mb-1.5 block text-xs font-medium text-ink-secondary">系统默认语言</label>
              <select
                value={form.language}
                onChange={(e) => update('language', e.target.value)}
                className={inputCls}
              >
                <option value="zh-CN">简体中文 (zh-CN)</option>
                <option value="en-US">English (US)</option>
              </select>
              <p className="mt-1 text-3xs text-ink-muted">控制平台术语与报表字段的本地化展示</p>
            </div>

            <div>
              <label className="mb-1.5 block text-xs font-medium text-ink-secondary">默认主市场</label>
              <select
                value={form.market}
                onChange={(e) => update('market', e.target.value)}
                className={inputCls}
              >
                <option value="A">中国 A 股市场 (CN Stock)</option>
                <option value="HK">香港股市 (HKEX)</option>
                <option value="US">美国证券市场 (US Equities)</option>
              </select>
              <p className="mt-1 text-3xs text-ink-muted">控制各工作台筛选器与行情的默认市场范围</p>
            </div>

            <div>
              <label className="mb-1.5 block text-xs font-medium text-ink-secondary">实时行情刷新频率</label>
              <select
                value={form.refresh_freq}
                onChange={(e) => update('refresh_freq', Number(e.target.value))}
                className={inputCls}
              >
                <option value={3}>每 3 秒 (极速高刷 · 交易台模式)</option>
                <option value={5}>每 5 秒 (标准推荐)</option>
                <option value={10}>每 10 秒 (节流省电)</option>
                <option value={30}>每 30 秒 (低频轮询)</option>
              </select>
              <p className="mt-1 text-3xs text-ink-muted">全局控制盘中实时行情与分时买卖挂单更新间隔</p>
            </div>
          </div>
        </SectionCard>
      </div>

      {/* 策略与交互偏好卡 */}
      <SectionCard
        title="投研交互与通知偏好"
        action={
          <div className="flex items-center gap-2">
            {isDirty && (
              <span className="text-2xs font-medium text-warn">● 存在未保存修改</span>
            )}
            <button
              type="button"
              onClick={handleSave}
              disabled={!isDirty || saving}
              className="rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600 disabled:opacity-50"
            >
              {saving ? '保存中…' : '保存偏好配置'}
            </button>
          </div>
        }
      >
        <div className="space-y-3">
          <div className="flex items-center justify-between rounded-lg border border-hair bg-surface-alt p-3">
            <div>
              <div className="text-xs font-semibold text-ink">策略回测完成浏览器/桌面弹窗提醒</div>
              <div className="text-2xs text-ink-muted">
                当后台长时间运行的 Walk-Forward 滚动回测或参数寻优作业完成时弹出完成提示
              </div>
            </div>
            <Toggle checked={form.notify_backtest} onChange={(v) => update('notify_backtest', v)} />
          </div>
        </div>
      </SectionCard>

      {/* 修改资料 Modal */}
      {profileModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-ink/40" onClick={() => setProfileModal(false)} />
          <div className="relative z-10 w-full max-w-sm rounded-lg border border-hair bg-surface p-5 shadow-xl">
            <h3 className="text-sm font-semibold text-ink">修改投研资料</h3>
            <div className="mt-3 space-y-3">
              <label className="block">
                <span className="mb-1 block text-xs text-ink-secondary">用户昵称</span>
                <input
                  type="text"
                  value={form.nickname}
                  onChange={(e) => update('nickname', e.target.value)}
                  className={inputCls}
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-xs text-ink-secondary">投研邮箱</span>
                <input
                  type="email"
                  value={form.email}
                  onChange={(e) => update('email', e.target.value)}
                  className={inputCls}
                />
              </label>
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setProfileModal(false)}
                className="rounded-md border border-hair px-3 py-1.5 text-xs text-ink-secondary hover:bg-surface-alt"
              >
                取消
              </button>
              <button
                type="button"
                onClick={async () => {
                  await handleSave();
                  setProfileModal(false);
                }}
                className="rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600"
              >
                保存资料
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
