/**
 * 系统设置（/settings）：个人偏好 / 数据接口连接 / 量化引擎 / 数据中心运维。
 *
 * 布局对照设计稿：标题 + 提示条 + 三列卡片网格 + 页脚。
 * 交互：
 *  - 主题切换即时生效（html.theme-dark 反色方案，auto 跟随系统），并计入待保存；
 *  - 「保存首选项」在偏好或引擎参数变更后高亮，一次性持久化 preferences + engine；
 *  - 数据源「测试连接」真实探测上游（AKShare→新浪指数切片 / 东财→push2delay），
 *    结果持久化，页面加载时自动静默测试；
 *  - 生成新密钥 / 清理所有缓存均带二次确认 Modal；新密钥明文仅展示一次；
 *  - 立即同步复用数据中心后台任务并轮询进度，完成后更新上次同步时间。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { datacenterApi } from '@/api/datacenter';
import {
  settingsApi, type ConnectorTest, type EngineConfig, type Preferences,
  type SystemStatus,
} from '@/api/settings';
import { useAuthStore } from '@/stores/useAuthStore';
import { usePreferencesStore } from '@/stores/usePreferencesStore';

/* ==================== 小件 ==================== */
function Card({ title, children, className = '' }: {
  title: string; children: React.ReactNode; className?: string;
}) {
  return (
    <div className={`flex h-full min-w-0 flex-col rounded-lg border border-hair bg-white ${className}`}>
      <div className="border-b border-hair px-4 py-3">
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
      </div>
      <div className="flex min-w-0 flex-1 flex-col p-4">{children}</div>
    </div>
  );
}

function Segmented<T extends string>({ value, onChange, options }: {
  value: T; onChange: (v: T) => void; options: Array<{ key: T; label: string }>;
}) {
  return (
    <div className="flex items-center gap-0.5 rounded-md border border-hair bg-slate-50 p-0.5">
      {options.map((o) => (
        <button key={o.key} onClick={() => onChange(o.key)}
          className={`flex-1 whitespace-nowrap rounded px-2 py-1 text-2xs transition-colors ${
            value === o.key ? 'bg-brand-500 font-medium text-white shadow-sm'
              : 'text-ink-secondary hover:text-ink'}`}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Toggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button role="switch" aria-checked={checked} onClick={() => onChange(!checked)}
      className={`relative h-5 w-9 rounded-full transition-colors ${checked ? 'bg-brand-500' : 'bg-slate-300'}`}>
      <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${
        checked ? 'left-[18px]' : 'left-0.5'}`} />
    </button>
  );
}

/** 状态徽标：已连接(绿)/连接中(琥珀·转圈)/连接异常(红)/未检测(灰) */
function StatusBadge({ test, testing }: { test?: ConnectorTest; testing: boolean }) {
  if (testing) return (
    <span className="flex items-center gap-1 rounded bg-amber-50 px-1.5 py-0.5 text-2xs text-amber-600">
      <svg className="h-2.5 w-2.5 animate-spin" viewBox="0 0 24 24" fill="none">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.4 0 0 5.4 0 12h4z" />
      </svg>
      连接中…
    </span>
  );
  if (!test) return <span className="rounded bg-slate-100 px-1.5 py-0.5 text-2xs text-ink-muted">未检测</span>;
  if (test.status === 'success') return (
    <span className="flex items-center gap-1 rounded bg-emerald-50 px-1.5 py-0.5 text-2xs font-medium text-emerald-600">
      <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" /> 已连接
    </span>
  );
  return (
    <span className="flex items-center gap-1 rounded bg-red-50 px-1.5 py-0.5 text-2xs font-medium text-red-500"
      title={test.message}>
      <span className="h-1.5 w-1.5 rounded-full bg-red-500" /> 连接异常
    </span>
  );
}

function Toast({ msg }: { msg: { text: string; kind: 'ok' | 'err' } | null }) {
  if (!msg) return null;
  return (
    <div className={`fixed bottom-6 right-6 z-50 rounded-md border px-4 py-2.5 text-xs shadow-lg ${
      msg.kind === 'ok' ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
        : 'border-red-200 bg-red-50 text-red-600'}`}>
      {msg.text}
    </div>
  );
}

function ConfirmModal({ open, title, body, confirmText, danger, onConfirm, onClose }: {
  open: boolean; title: string; body: string; confirmText: string; danger?: boolean;
  onConfirm: () => void; onClose: () => void;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-slate-900/40" onClick={onClose} />
      <div className="relative z-10 w-full max-w-sm rounded-lg border border-hair bg-white p-5 shadow-xl">
        <h3 className="text-sm font-semibold text-ink">{title}</h3>
        <p className="mt-2 text-xs leading-relaxed text-ink-secondary">{body}</p>
        <div className="mt-4 flex justify-end gap-2">
          <button onClick={onClose}
            className="rounded-md border border-hair px-3 py-1.5 text-xs text-ink-secondary hover:bg-slate-50">取消</button>
          <button onClick={() => { onConfirm(); onClose(); }}
            className={`rounded-md px-3 py-1.5 text-xs font-medium text-white ${
              danger ? 'bg-red-500 hover:bg-red-600' : 'bg-brand-500 hover:bg-brand-600'}`}>
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}

const RISK_OPTIONS = [
  { key: 'annual', label: '年化收益' },
  { key: 'max_dd', label: '最大回撤' },
  { key: 'sharpe', label: 'Sharpe' },
  { key: 'sortino', label: 'Sortino' },
  { key: 'win_rate', label: '胜率' },
];

/* ==================== 主页面 ==================== */
export default function Settings() {
  const navigate = useNavigate();
  const authUser = useAuthStore((s) => s.user);
  // 历史问题（审计 F8）：此前这里写死 nickname:'Quant User',
  // email:'quant_alpha_user@platform.com'，接口失败时页面照样显示一个
  // "看起来已登录"的假账户。现在默认值取自真实登录态（/auth/login 签发），
  // 接口失败时显示真实用户名与空邮箱，不再伪装。
  const [prefs, setPrefs] = useState<Preferences>({
    theme: 'light', language: 'zh-CN', market: 'A', notify_backtest: true,
    refresh_freq: 3, nickname: authUser?.username ?? '未登录', email: '',
  });
  const [engine, setEngine] = useState<EngineConfig>({
    name: 'vectorbt', risk_indicators: ['annual', 'sortino'],
    commission_pct: 0.03, slippage_pct: 0.1,
  });
  const [apiKeys, setApiKeys] = useState<Array<{ masked: string; created: string }>>([]);
  const [tests, setTests] = useState<Record<string, ConnectorTest>>({});
  const [testing, setTesting] = useState<Record<string, boolean>>({});
  const [storageGb, setStorageGb] = useState<number | null>(null);
  const [lastSync, setLastSync] = useState<string | null>(null);
  /** 缓存降级 + 磁盘水位（L1-1 / §6.1）：null = 后端未提供（旧版本兼容） */
  const [system, setSystem] = useState<SystemStatus | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ text: string; kind: 'ok' | 'err' } | null>(null);
  const [confirm, setConfirm] = useState<'key' | 'cache' | null>(null);
  const [newKey, setNewKey] = useState<string | null>(null);
  const [profileOpen, setProfileOpen] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncProg, setSyncProg] = useState('');
  const [cacheDays, setCacheDays] = useState(30);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const notify = useCallback((text: string, kind: 'ok' | 'err' = 'ok') => {
    setToast({ text, kind });
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setToast(null), 3200);
  }, []);

  /* ---------- 主题即时生效 ---------- */
  const applyTheme = useCallback((theme: Preferences['theme']) => {
    const root = document.documentElement;
    const dark = theme === 'dark'
      || (theme === 'auto' && window.matchMedia('(prefers-color-scheme: dark)').matches);
    root.classList.toggle('theme-dark', dark);
  }, []);
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const fn = () => { if (prefs.theme === 'auto') applyTheme('auto'); };
    mq.addEventListener('change', fn);
    return () => mq.removeEventListener('change', fn);
  }, [prefs.theme, applyTheme]);

  /* ---------- 初始化加载 + 静默连接测试 ---------- */
  const testAll = useCallback(async () => {
    setTesting({ akshare: true, eastmoney: true });
    const results = await Promise.allSettled([
      settingsApi.testConnector('akshare'), settingsApi.testConnector('eastmoney'),
    ]);
    setTesting({});
    setTests((cur) => {
      const next = { ...cur };
      results.forEach((r) => {
        if (r.status === 'fulfilled') next[r.value.connector] = r.value;
      });
      return next;
    });
  }, []);

  const load = useCallback(async (withTest: boolean) => {
    try {
      const bundle = await settingsApi.all();
      setPrefs(bundle.settings.preferences);
      setEngine(bundle.settings.engine);
      setApiKeys(bundle.settings.api_keys ?? []);
      setTests(bundle.settings.last_tests ?? {});
      setStorageGb(bundle.storage_gb);
      setLastSync(bundle.last_sync);
      setSystem(bundle.system ?? null);
      applyTheme(bundle.settings.preferences.theme);
      if (withTest) void testAll();
    } catch {
      notify('设置加载失败，请检查后端服务', 'err');
    }
  }, [applyTheme, notify, testAll]);

  useEffect(() => { void load(true); }, [load]);
  // 浏览器滚动恢复会把标题顶出 sticky 顶栏，强制回到页顶
  useEffect(() => { window.scrollTo(0, 0); }, []);

  const markDirty = () => setDirty(true);

  /* ---------- 保存首选项（preferences + engine 一起） ---------- */
  const saveAll = async () => {
    setSaving(true);
    try {
      await Promise.all([
        settingsApi.savePreferences(prefs),
        settingsApi.saveEngine(engine),
      ]);
      setDirty(false);
      // P1-5：保存后立即写入偏好 store，各页面轮询间隔即时生效（zustand 广播）
      usePreferencesStore.getState().setRefreshFreq(prefs.refresh_freq);
      notify('首选项已保存，行情刷新频率已即时生效');
    } catch {
      notify('保存失败，请重试', 'err');
    } finally { setSaving(false); }
  };

  /* ---------- 连接测试（单个） ---------- */
  const runTest = async (connector: 'akshare' | 'eastmoney') => {
    setTesting((t) => ({ ...t, [connector]: true }));
    try {
      const r = await settingsApi.testConnector(connector);
      setTests((cur) => ({ ...cur, [connector]: r }));
    } catch { setTests((cur) => ({ ...cur, [connector]: { status: 'error', latency_ms: null } })); }
    finally { setTesting((t) => ({ ...t, [connector]: false })); }
  };

  /* ---------- 数据运维 ---------- */
  const runSync = async () => {
    if (syncing) return;
    try {
      await settingsApi.syncDaily();
      setSyncing(true);
      const poll = setInterval(async () => {
        try {
          const st = await datacenterApi.status();
          setSyncProg(st.running ? `${st.done}/${st.total}` : '');
          if (!st.running) {
            clearInterval(poll);
            setSyncing(false); setSyncProg('');
            const bundle = await settingsApi.all();
            setLastSync(bundle.last_sync); setStorageGb(bundle.storage_gb);
            notify('当日日线增量同步完成');
          }
        } catch { /* 忽略单次轮询失败 */ }
      }, 1500);
    } catch (e) {
      setSyncing(false);
      notify(e instanceof Error ? e.message : '同步启动失败', 'err');
    }
  };

  const rotateKey = async () => {
    try {
      const r = await settingsApi.rotateKey();
      setApiKeys(r.keys);
      setNewKey(r.key);
    } catch { notify('密钥生成失败', 'err'); }
  };

  const clearCache = async () => {
    try {
      const r = await settingsApi.clearCache();
      notify(r.message);
    } catch { notify('缓存清理失败', 'err'); }
  };

  const backup = async () => {
    try {
      const r = await settingsApi.backupDb();
      notify(`备份完成：${r.file}（${r.size_mb} MB）`);
    } catch { notify('备份失败', 'err'); }
  };

  const inputCls = 'w-full rounded-md border border-hair bg-white px-2.5 py-1.5 text-xs text-ink outline-none focus:border-brand-300';

  return (
    <div className="flex min-h-full flex-col gap-3">
      <h1 className="text-lg font-bold text-ink">系统设置</h1>

      <div className="flex items-center gap-2 rounded-md border border-blue-100 bg-blue-50/70 px-3 py-1.5 text-xs text-ink-secondary">
        <span className="text-amber-400">💡</span>
        <span>设置平台偏好与数据接口连接</span>
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        {/* ============ 列 1：个人与平台偏好 ============ */}
        <Card title="个人与平台偏好">
          <div className="flex items-center gap-3 border-b border-hair pb-4">
            <div className="flex h-14 w-14 items-center justify-center rounded-full bg-brand-100">
              <svg viewBox="0 0 24 24" className="h-8 w-8 text-brand-500" fill="currentColor">
                <path d="M12 12a4.5 4.5 0 1 0-4.5-4.5A4.5 4.5 0 0 0 12 12Zm0 2c-4 0-7.5 2-7.5 4.5V21h15v-2.5C19.5 16 16 14 12 14Z" />
              </svg>
            </div>
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold text-ink">{prefs.nickname}</div>
              <div className="truncate text-xs text-ink-muted">{prefs.email}</div>
              <button onClick={() => setProfileOpen(true)}
                className="mt-1 rounded border border-hair px-2 py-0.5 text-2xs text-ink-secondary hover:border-brand-200 hover:text-brand-600">
                修改资料
              </button>
            </div>
          </div>

          <div className="space-y-4 pt-4">
            <div>
              <div className="mb-1.5 text-xs text-ink-secondary">平台主题</div>
              <Segmented value={prefs.theme}
                onChange={(v) => { setPrefs({ ...prefs, theme: v }); applyTheme(v); markDirty(); }}
                options={[
                  { key: 'light', label: '浅色模式' },
                  { key: 'dark', label: '深色模式' },
                  { key: 'auto', label: '自动同步' },
                ]} />
            </div>
            <div>
              <div className="mb-1.5 text-xs text-ink-secondary">默认语言</div>
              <select value={prefs.language}
                onChange={(e) => { setPrefs({ ...prefs, language: e.target.value }); markDirty(); }}
                className={inputCls}>
                <option value="zh-CN">中文（简体）</option>
                <option value="en-US">English (US)</option>
              </select>
            </div>
            <div>
              <div className="mb-1.5 text-xs text-ink-secondary">默认市场</div>
              <select value={prefs.market}
                onChange={(e) => { setPrefs({ ...prefs, market: e.target.value }); markDirty(); }}
                className={inputCls}>
                <option value="A">A股市场</option>
                <option value="HK">港股市场</option>
                <option value="US">美股市场</option>
              </select>
            </div>
            <div>
              <div className="mb-1.5 text-xs text-ink-secondary">通知</div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-ink">接收策略回测完成通知</span>
                <Toggle checked={prefs.notify_backtest}
                  onChange={(v) => { setPrefs({ ...prefs, notify_backtest: v }); markDirty(); }} />
              </div>
            </div>
            <button onClick={() => void saveAll()} disabled={!dirty || saving}
              className="mt-auto w-full rounded-md bg-brand-500 py-2 text-xs font-medium text-white
                transition-all hover:bg-brand-600 disabled:cursor-not-allowed disabled:bg-slate-200 disabled:text-slate-400">
              {saving ? '保存中…' : dirty ? '保存首选项 ●' : '保存首选项'}
            </button>
          </div>
        </Card>

        {/* ============ 列 2：数据接口与连接 ============ */}
        <div className="flex min-w-0 flex-col gap-3">
          <Card title="数据接口与连接">
            {/* AKShare */}
            <div className="rounded-md border border-emerald-200 bg-emerald-50/60 p-3">
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-1.5 text-xs font-semibold text-ink">
                  <span className="flex h-4 w-4 items-center justify-center rounded bg-emerald-500 text-[8px] font-bold text-white">AK</span>
                  AKShare
                </span>
                <StatusBadge test={tests.akshare} testing={!!testing.akshare} />
              </div>
              <div className="mt-2 flex items-center justify-between text-2xs text-ink-muted">
                <span>数据源：AKShare（{tests.akshare?.latency_ms != null ? `${tests.akshare.latency_ms}ms` : '点击测试'}）</span>
                <button onClick={() => void runTest('akshare')} disabled={!!testing.akshare}
                  className="rounded border border-hair bg-white px-2 py-0.5 text-2xs text-ink-secondary hover:border-brand-200 hover:text-brand-600 disabled:opacity-50">
                  测试连接
                </button>
              </div>
              <div className="mt-1.5 flex items-center justify-between text-2xs text-ink-muted">
                <span>实时行情刷新的频率: 每{prefs.refresh_freq}秒</span>
                <select value={prefs.refresh_freq}
                  onChange={(e) => { setPrefs({ ...prefs, refresh_freq: Number(e.target.value) }); markDirty(); }}
                  className="rounded border border-hair bg-white px-1 py-0.5 text-2xs text-brand-600 outline-none">
                  {[3, 5, 10].map((s) => <option key={s} value={s}>管理配置</option>)}
                </select>
              </div>
            </div>

            {/* Eastmoney */}
            <div className="mt-2.5 rounded-md border border-amber-200 bg-amber-50/60 p-3">
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-1.5 text-xs font-semibold text-ink">
                  <span className="flex h-4 w-4 items-center justify-center rounded bg-amber-500 text-[8px] font-bold text-white">EM</span>
                  Eastmoney
                </span>
                <StatusBadge test={tests.eastmoney} testing={!!testing.eastmoney} />
              </div>
              <div className="mt-2 flex items-center justify-between text-2xs text-ink-muted">
                <span>数据源：东方财富数据{tests.eastmoney?.latency_ms != null ? `（${tests.eastmoney.latency_ms}ms）` : ''}</span>
                <button onClick={() => void runTest('eastmoney')} disabled={!!testing.eastmoney}
                  className="rounded border border-hair bg-white px-2 py-0.5 text-2xs text-ink-secondary hover:border-brand-200 hover:text-brand-600 disabled:opacity-50">
                  重试连接
                </button>
              </div>
              <div className="mt-1.5 flex items-center justify-between text-2xs text-ink-muted">
                <span>实时行情刷新的频率: 每{prefs.refresh_freq}秒</span>
                <span className="cursor-pointer text-brand-600 hover:underline"
                  onClick={() => notify('刷新频率在左侧「实时行情刷新的频率」处统一配置')}>[管理配置]</span>
              </div>
            </div>

            {/* QuantConnect/IB（未启用） */}
            <div className="mt-2.5 rounded-md border border-hair bg-slate-50 p-3 opacity-70">
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-1.5 text-xs font-medium text-ink-muted">
                  <span className="font-mono text-2xs">&lt;/&gt;</span> QuantConnect/IB
                </span>
                <button disabled
                  className="rounded border border-hair bg-white px-2 py-0.5 text-2xs text-ink-muted/60"
                  title="外部券商接口为规划功能">
                  启用外部接口
                </button>
              </div>
              <div className="mt-1.5 text-2xs text-ink-muted/70">数据源：高级外部 API（暂未开放）</div>
            </div>

            {/* API Keys */}
            <div className="mt-3 border-t border-hair pt-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-ink">API Keys</span>
                <button onClick={() => setConfirm('key')}
                  className="rounded border border-brand-200 bg-brand-50 px-2 py-0.5 text-2xs text-brand-600 hover:bg-brand-100">
                  生成新密钥
                </button>
              </div>
              <div className="mt-2 space-y-1 rounded-md bg-slate-50 p-2.5">
                {apiKeys.length ? apiKeys.slice(-3).map((k, i) => (
                  <div key={i} className="num flex items-center justify-between text-2xs text-ink-secondary">
                    <span>Key: {k.masked}</span>
                    <span className="text-ink-muted/70">{k.created}</span>
                  </div>
                )) : (
                  <div className="text-2xs text-ink-muted">暂无密钥，点击「生成新密钥」创建</div>
                )}
              </div>
              <div className="mt-1 text-2xs text-ink-muted">管理 API 密钥（用于系统管理类接口鉴权）</div>
            </div>

            <button onClick={() => void testAll()} disabled={!!(testing.akshare || testing.eastmoney)}
              className="mt-auto w-full rounded-md border border-hair py-2 text-xs text-ink-secondary
                transition-colors hover:border-brand-200 hover:text-brand-600 disabled:opacity-50">
              刷新所有连接数据
            </button>
          </Card>
        </div>

        {/* ============ 列 3：量化引擎 + 数据中心 ============ */}
        <div className="flex min-w-0 flex-col gap-3">
          <Card title="量化引擎设置">
            <div>
              <div className="mb-1.5 text-xs text-ink-secondary">标准回测引擎</div>
              <select value={engine.name}
                onChange={(e) => { setEngine({ ...engine, name: e.target.value }); markDirty(); }}
                className={inputCls}>
                <option value="vectorbt">VECTORBT（默认，极速版）</option>
                <option value="backtrader">BACKTRADER（事件驱动）</option>
                <option value="aqp">AQP 自研引擎（Top-K 等权）</option>
              </select>
              <p className="mt-1 text-2xs text-ink-muted">
                当前平台策略回测由 AQP 自研 MA 交叉引擎执行；此默认值用于后续扩展引擎接入。
              </p>
            </div>
            <div className="mt-3">
              <div className="mb-1.5 text-xs text-ink-secondary">风险指标：默认图表展示项</div>
              <div className="grid grid-cols-2 gap-1.5">
                {RISK_OPTIONS.map((o) => (
                  <label key={o.key} className="flex cursor-pointer items-center gap-1.5 text-xs text-ink">
                    <input type="checkbox" checked={engine.risk_indicators.includes(o.key)}
                      onChange={(e) => {
                        setEngine({
                          ...engine,
                          risk_indicators: e.target.checked
                            ? [...engine.risk_indicators, o.key]
                            : engine.risk_indicators.filter((k) => k !== o.key),
                        });
                        markDirty();
                      }}
                      className="h-3.5 w-3.5 accent-brand-500" />
                    {o.label}
                  </label>
                ))}
              </div>
            </div>
            <div className="mt-3">
              <div className="mb-1.5 text-xs text-ink-secondary">摩擦构模型</div>
              <div className="flex gap-4">
                <label className="flex flex-1 items-center gap-1.5 text-xs text-ink-secondary">
                  佣金：
                  <input type="number" min={0.01} max={1} step={0.01} value={engine.commission_pct}
                    onChange={(e) => { setEngine({ ...engine, commission_pct: Number(e.target.value) }); markDirty(); }}
                    className="w-20 rounded border border-hair px-1.5 py-1 text-right text-xs text-ink outline-none focus:border-brand-300" />
                  %
                </label>
                <label className="flex flex-1 items-center gap-1.5 text-xs text-ink-secondary">
                  滑点：
                  <input type="number" min={0} max={2} step={0.05} value={engine.slippage_pct}
                    onChange={(e) => { setEngine({ ...engine, slippage_pct: Number(e.target.value) }); markDirty(); }}
                    className="w-20 rounded border border-hair px-1.5 py-1 text-right text-xs text-ink outline-none focus:border-brand-300" />
                  %
                </label>
              </div>
            </div>
          </Card>

          <Card title="数据中心与缓存管理">
            <div className="flex items-start justify-between">
              <div>
                <div className="text-xs text-ink-secondary">本地 DB 状态</div>
                <div className="mt-1 flex items-center gap-1.5 text-xs font-medium text-emerald-600">
                  <span className="h-2 w-2 animate-pulse rounded-full bg-emerald-500" />
                  数据库正常
                </div>
                <div className="num mt-1 text-2xs text-ink-muted">
                  缓存数据量：{storageGb != null ? `${storageGb.toFixed(1)} GB` : '—'}
                </div>
              </div>
              <button onClick={() => navigate('/data')}
                className="rounded border border-hair px-2.5 py-1 text-2xs text-ink-secondary hover:border-brand-200 hover:text-brand-600">
                查看数据状态
              </button>
            </div>

            <div className="mt-3 border-t border-hair pt-3">
              <div className="mb-1.5 text-xs text-ink-secondary">系统健康</div>
              <div className="flex flex-wrap items-center gap-2 text-2xs">
                {/* 缓存状态（L1-1）：Redis 不可用即降级为进程内 LRU，重启即失效 */}
                {system ? (
                  system.cache.degraded ? (
                    <span className="rounded bg-amber-50 px-1.5 py-0.5 font-medium text-amber-700"
                      title="Redis 不可用，缓存已降级为进程内 LRU（重启即失效）。建议：docker start aqp-redis（或 docker-compose start redis）">
                      ⚠ 缓存降级
                    </span>
                  ) : system.cache.enabled ? (
                    <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-emerald-700">缓存正常</span>
                  ) : (
                    <span className="rounded bg-slate-100 px-1.5 py-0.5 text-ink-muted">缓存已关闭</span>
                  )
                ) : null}
                {/* 磁盘水位（§6.1）：>90% warning / >95% critical */}
                {system?.disk?.usage_percent != null && (
                  <span className={`rounded px-1.5 py-0.5 ${
                    system.disk.level === 'critical'
                      ? 'bg-red-50 font-medium text-red-600'
                      : system.disk.level === 'warning'
                        ? 'bg-amber-50 text-amber-700'
                        : 'bg-slate-100 text-ink-muted'}`}>
                    磁盘 {system.disk.usage_percent.toFixed(0)}%
                    {system.disk.level === 'critical' ? '（紧急）'
                      : system.disk.level === 'warning' ? '（偏高）' : ''}
                  </span>
                )}
                {!system && <span className="text-ink-muted">状态不可用</span>}
              </div>
            </div>

            <div className="mt-3 border-t border-hair pt-3">
              <div className="mb-1.5 text-xs text-ink-secondary">手动同步</div>
              <button onClick={() => void runSync()} disabled={syncing}
                className="w-full rounded-md border border-hair py-1.5 text-xs text-ink transition-colors
                  hover:border-brand-200 hover:text-brand-600 disabled:cursor-not-allowed disabled:opacity-60">
                {syncing ? `正在增量同步 A 股日线数据 (${syncProg || '准备中'})…` : '立即同步当日日线数据（15:00之后）'}
              </button>
              <div className="num mt-1 text-right text-2xs text-ink-muted">
                上次同步时间: {lastSync ? lastSync.replace('T', ' ').slice(5, 19) : '—'}
              </div>
            </div>

            <div className="mt-3 border-t border-hair pt-3">
              <div className="mb-1.5 text-xs text-ink-secondary">缓存清理</div>
              <div className="flex items-center gap-3">
                <div className="min-w-0 flex-1">
                  <div className="text-2xs text-ink-muted">
                    自动清理缓存（{cacheDays}天前数据）
                  </div>
                  <input type="range" min={7} max={90} step={1} value={cacheDays}
                    onChange={(e) => setCacheDays(Number(e.target.value))}
                    className="mt-1 w-full accent-brand-500" />
                </div>
                <button onClick={() => setConfirm('cache')}
                  className="shrink-0 rounded-md border border-red-200 px-2.5 py-1.5 text-2xs text-red-500 hover:bg-red-50">
                  清理所有缓存
                </button>
              </div>
            </div>

            <div className="mt-auto flex items-center justify-between border-t border-hair pt-3">
              <span className="text-xs text-ink-secondary">数据库工具</span>
              <button onClick={() => void backup()}
                className="rounded border border-hair px-2.5 py-1 text-2xs text-ink-secondary hover:border-brand-200 hover:text-brand-600">
                备份所有策略数据
              </button>
            </div>
          </Card>
        </div>
      </div>

      {/* 页脚 */}
      <div className="flex flex-wrap items-center justify-between gap-1 pb-1 text-2xs text-ink-muted">
        <span>系统设置由 Alpha Quant Platform 提供 ｜ AQP 量化分析系统 v2.3</span>
        <span>结果仅供参考，不作为投资依据</span>
      </div>

      {/* ---------- 弹窗区 ---------- */}
      <Toast msg={toast} />

      <ConfirmModal open={confirm === 'key'} title="生成新 API 密钥"
        body="将生成新的系统管理密钥，旧密钥保持有效。新密钥明文仅在生成后展示一次，请立即妥善保存。"
        confirmText="生成密钥" onConfirm={() => void rotateKey()} onClose={() => setConfirm(null)} />

      <ConfirmModal open={confirm === 'cache'} title="清理所有缓存"
        body="将删除 Redis 中全部 AQP 行情缓存切片与进程内缓存（不影响本地行情数据库）。清理后首次访问相关页面会重新拉取数据，速度稍慢。"
        confirmText="确认清理" danger onConfirm={() => void clearCache()} onClose={() => setConfirm(null)} />

      {/* 新密钥展示（仅一次） */}
      {newKey && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-slate-900/40" onClick={() => setNewKey(null)} />
          <div className="relative z-10 w-full max-w-md rounded-lg border border-hair bg-white p-5 shadow-xl">
            <h3 className="text-sm font-semibold text-ink">新密钥已生成</h3>
            <p className="mt-1.5 text-xs text-ink-secondary">请立即复制保存，关闭后将无法再次查看完整密钥：</p>
            <div className="num mt-2.5 break-all rounded-md bg-slate-900 p-3 text-xs text-emerald-300">{newKey}</div>
            <div className="mt-3 flex justify-end gap-2">
              <button onClick={() => { void navigator.clipboard.writeText(newKey); notify('密钥已复制到剪贴板'); }}
                className="rounded-md border border-hair px-3 py-1.5 text-xs text-ink-secondary hover:bg-slate-50">复制</button>
              <button onClick={() => setNewKey(null)}
                className="rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600">我已保存</button>
            </div>
          </div>
        </div>
      )}

      {/* 修改资料 */}
      {profileOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-slate-900/40" onClick={() => setProfileOpen(false)} />
          <div className="relative z-10 w-full max-w-sm rounded-lg border border-hair bg-white p-5 shadow-xl">
            <h3 className="text-sm font-semibold text-ink">修改资料</h3>
            <div className="mt-3 space-y-2.5">
              <label className="block">
                <span className="mb-1 block text-xs text-ink-secondary">昵称</span>
                <input value={prefs.nickname} onChange={(e) => setPrefs({ ...prefs, nickname: e.target.value })}
                  className={inputCls} />
              </label>
              <label className="block">
                <span className="mb-1 block text-xs text-ink-secondary">邮箱</span>
                <input value={prefs.email} onChange={(e) => setPrefs({ ...prefs, email: e.target.value })}
                  className={inputCls} />
              </label>
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button onClick={() => setProfileOpen(false)}
                className="rounded-md border border-hair px-3 py-1.5 text-xs text-ink-secondary hover:bg-slate-50">取消</button>
              <button onClick={async () => {
                try {
                  await settingsApi.savePreferences(prefs);
                  setProfileOpen(false);
                  notify('资料已更新');
                } catch { notify('保存失败', 'err'); }
              }}
                className="rounded-md bg-brand-500 px-3 py-1.5 text-xs font-medium text-white hover:bg-brand-600">保存</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
