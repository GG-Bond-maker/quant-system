/**
 * ETF 筛选器：投资类型 / 跟踪指数 / 管理公司 / 规模区间 / 成立日期区间。
 *
 * 跟踪指数与管理公司下拉项来自后端 options（由当前目录聚合，含中/美/日/韩）。
 * 规模与成立日期为可选条件，留空即不限制。
 *
 * 🔴 交互模型 = **草稿 + 显式应用**（P1 修复，2026-10-02）
 * 本面板底部有「筛选」按钮，说明设计意图是「填完再应用」。原实现让每个控件
 * 直接 `onChange({...value, [k]: v})` 立即回写父级，而「筛选」按钮执行
 * `onClick={() => onChange(value)}` —— 传回的正是父级**已经持有的同一个对象引用**，
 * React 以 `Object.is` 判定无变化直接 bail out ⇒ **点击零反应（欺骗性 UI）**。
 *
 * 现改为：所有控件只改本地草稿 `draft`；「筛选」才把草稿提交给父级。
 * 由此同时修掉两个连带缺陷：
 *   1. 数字/日期输入框**每敲一键**就触发一次列表请求（逐字外呼）；
 *   2. 每次筛选变化先以旧页码请求一次、再因页码复位请求第二次。
 *
 * ⚠️ 字段归属：`q`（关键字）由页面顶部的搜索框拥有，**不在本面板编辑**。
 * 提交时显式沿用父级的 `value.q`，避免用草稿里的陈旧副本把搜索框内容覆盖回去。
 */
import { useState } from 'react';
import { PanelEmpty } from '@/components/ui';

export interface EtfFilters {
  etype: string;
  index: string;
  manager: string;
  min_size: string;
  max_size: string;
  inception_from: string;
  inception_to: string;
  q: string;
}

/** 面板「重置条件」的归一化初值（与页面 onReset 保持同一口径） */
export const EMPTY_ETF_FILTERS: EtfFilters = {
  etype: 'all', index: '', manager: '', min_size: '', max_size: '',
  inception_from: '', inception_to: '', q: '',
};

const TYPES = [
  { key: 'all', label: '全部' },
  { key: '股票型', label: '股票型' },
  { key: '债券型', label: '债券型' },
  { key: '商品型', label: '商品型' },
  { key: '货币型', label: '货币型' },
  { key: '跨境型', label: '跨境型' },
];

// py-1.5 → py-1：本面板有 4 个输入框 + 2 个日期框，每个省 4px 共省 ~24px 纵向
const inputCls = 'w-full rounded border border-hair bg-surface px-1.5 py-1 text-xs text-ink outline-none focus:border-brand-300';
// 行标签：与输入框同排的紧凑标签（2-xs + 固定宽度对齐），替代"标签独占一行"
const labelCls = 'w-14 shrink-0 text-2xs text-ink-secondary';

export default function FilterPanel({ options, value, onChange, onReset }: {
  options: { boards: string[]; types: string[]; indexes: string[]; managers: string[] } | null | undefined;
  value: EtfFilters;
  onChange: (v: EtfFilters) => void;
  onReset: () => void;
}) {
  /** 本地草稿：初值取父级当前值；此后只有「筛选」才回写父级 */
  const [draft, setDraft] = useState<EtfFilters>(value);
  const set = <K extends keyof EtfFilters>(k: K, v: EtfFilters[K]) =>
    setDraft((d) => ({ ...d, [k]: v }));

  /** 提交草稿。`q` 始终沿用父级现值 —— 该字段由顶部搜索框拥有，见文件头注释 */
  const apply = () => onChange({ ...draft, q: value.q });

  /** 重置：草稿与父级同时归零（父级的 onReset 是权威写入点） */
  const reset = () => {
    setDraft(EMPTY_ETF_FILTERS);
    onReset();
  };

  /** 草稿与已应用值是否有差异（用于提示"有未应用的修改"） */
  const dirty = (Object.keys(EMPTY_ETF_FILTERS) as (keyof EtfFilters)[])
    .some((k) => k !== 'q' && draft[k] !== value[k]);

  return (
    <div className="flex min-w-0 flex-col rounded-lg border border-hair bg-surface">
      <div className="flex items-center justify-between border-b border-hair px-2.5 py-1.5">
        <h3 className="text-xs font-semibold text-ink">ETF筛选器</h3>
        <button onClick={reset} className="text-2xs text-brand-600 hover:underline">重置</button>
      </div>
      {/* space-y-2.5 → space-y-2：整体收紧一档，配合下面的"标签左置"进一步降高 */}
      <div className="space-y-2 p-2.5">
        {/* 🔴 作用域声明（P1 修复，2026-10-02）：本面板 + 顶部板块/国家条件**只**作用于
            「ETF列表」与「热门榜」。若不作此声明，用户切到「美国」后会默认整页都跟着变，
            而「资金流向」（全市场榜）与「规模变化」（仅 A 股）实际不动 —— 那是把未筛选的
            数据当成筛选结果读。两张卡各自另有后端回显的「口径：…」行，此处给出总纲。 */}
        <p className="rounded bg-surface-sunken px-1.5 py-1 text-2xs leading-tight text-ink-muted">
          条件作用于「ETF列表」与「热门榜」；资金流向、规模变化为固定口径，不随之变化
        </p>
        {/* 投资类型 */}
        <div>
          <div className="mb-1 text-2xs text-ink-secondary">投资类型</div>
          <div className="flex flex-wrap gap-1">
            {TYPES.map((t) => (
              <button key={t.key} onClick={() => set('etype', t.key)}
                className={`rounded px-1.5 py-0.5 text-2xs transition-colors ${
                  draft.etype === t.key
                    ? 'bg-brand-500 text-white'
                    : 'bg-surface-sunken text-ink-secondary hover:bg-surface-sunken'}`}>
                {t.label}
              </button>
            ))}
          </div>
        </div>

        {/* 跟踪指数 / 管理公司：标签左置同排，省掉两行标签高度（~32px） */}
        <label className="flex items-center gap-1.5">
          <span className={labelCls}>跟踪指数</span>
          <select value={draft.index} onChange={(e) => set('index', e.target.value)}
            className={inputCls}>
            <option value="">全部指数</option>
            {(options?.indexes ?? []).map((i) => <option key={i} value={i}>{i}</option>)}
          </select>
        </label>

        <label className="flex items-center gap-1.5">
          <span className={labelCls}>管理公司</span>
          <select value={draft.manager} onChange={(e) => set('manager', e.target.value)}
            className={inputCls}>
            <option value="">全部公司</option>
            {(options?.managers ?? []).map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </label>

        {/* 规模区间：标签左置，两个输入框各占半宽 */}
        <div className="flex items-center gap-1.5">
          <span className={labelCls}>规模(亿)</span>
          <div className="flex min-w-0 flex-1 items-center gap-1">
            <input type="number" min={0} placeholder="不限" value={draft.min_size}
              onChange={(e) => set('min_size', e.target.value)} className={inputCls} />
            <span className="text-2xs text-ink-muted">—</span>
            <input type="number" min={0} placeholder="不限" value={draft.max_size}
              onChange={(e) => set('max_size', e.target.value)} className={inputCls} />
          </div>
        </div>

        {/* 成立日期：同上，标签左置 */}
        <div className="flex items-center gap-1.5">
          <span className={labelCls}>成立日期</span>
          <div className="flex min-w-0 flex-1 items-center gap-1">
            <input type="date" value={draft.inception_from}
              onChange={(e) => set('inception_from', e.target.value)} className={inputCls} />
            <span className="text-2xs text-ink-muted">—</span>
            <input type="date" value={draft.inception_to}
              onChange={(e) => set('inception_to', e.target.value)} className={inputCls} />
          </div>
        </div>
        <p className="text-2xs leading-tight text-ink-muted">
          仅中/美国 ETF 提供成立日期，日韩目录暂无该字段
        </p>

        <div className="flex gap-1.5 pt-0.5">
          <button onClick={apply}
            className="flex-1 rounded bg-brand-500 py-1.5 text-xs text-white hover:bg-brand-600">
            筛选{dirty ? ' •' : ''}
          </button>
          <button onClick={reset}
            className="flex-1 rounded border border-hair bg-surface py-1.5 text-xs text-ink-secondary hover:bg-surface-alt">
            重置条件
          </button>
        </div>

        {!options && <PanelEmpty text="筛选项加载中" minH="min-h-[40px]" />}
      </div>
    </div>
  );
}
