/**
 * 外盘指数区块 —— 逐卡日期 + 降级不冒充 + 无「实时」字样（2026-10-04）。
 *
 * ## 被守的四件事
 * 1. **每张卡展示自己的 `data_date`**：不同市场交易日/时区不同（实测美股/日韩
 *    10-02、A 股 09-30），共用 A 股 trade_date 会把"上一交易日的外盘收盘"
 *    错标成"A 股当日"。夹具刻意让纳指停在 09-30、其余停在 10-02。
 * 2. **降级不冒充**：`status !== "ok"` 时 5 张卡全渲染「—」，不得出现 `0.00` / `+0.00%`。
 * 3. **有 `as_of` ⇒ 展示源时间戳（`10-02 17:00 更新`）；无 ⇒ `截至 10-02 收盘`**。
 *    两者**都不得**写成「实时」：北京时间白天美股休市，那个时间戳就是上一收盘时刻。
 * 4. **来源如实披露在 `title`**（腾讯行情 / 新浪财经）。
 *
 * ⚠️ jsdom 无布局引擎 ⇒ 只守结构与文案，"真的排成一行"待人工目视验收。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { GlobalIndexItem, GlobalIndicesBlock } from '@/types/stock';

import GlobalIndices from './GlobalIndices';

const mk = (code: string, name: string, close: number, pct: number,
            data_date: string, extra?: Partial<GlobalIndexItem>): GlobalIndexItem =>
  ({ code, name, close, pct, data_date, sparkline: [1, 2, 3, 4], ...extra });

const OK: GlobalIndicesBlock = {
  status: 'ok',
  items: [
    mk('日经225指数', '日经225 (NKY)', 39800.5, 1.23, '2026-10-02'),
    mk('首尔综合指数', '韩国KOSPI', 3421.1, -0.45, '2026-10-02'),
    mk('.DJI', '道琼斯 (.DJI)', 42000.0, 0.5, '2026-10-02'),
    mk('.IXIC', '纳斯达克 (.IXIC)', 18000.0, -1.1, '2026-09-30'), // 故意与其余不同
    mk('.INX', '标普500 (.INX)', 5700.0, 0.2, '2026-10-02'),
  ],
  failed: [],
};

/** 美股 3 项带腾讯报价时间戳；日经/KOSPI 只有日线收盘。 */
const OK_MIXED: GlobalIndicesBlock = {
  status: 'ok',
  items: [
    mk('日经225指数', '日经225 (NKY)', 39800.5, 1.23, '2026-10-02',
       { source: 'sina', as_of: null }),
    mk('首尔综合指数', '韩国KOSPI', 3421.1, -0.45, '2026-10-02',
       { source: 'sina', as_of: null }),
    mk('.DJI', '道琼斯 (.DJI)', 51176.96, 0.49, '2026-10-02',
       { source: 'tencent', as_of: '2026-10-02 17:00:17' }),
    mk('.IXIC', '纳斯达克 (.IXIC)', 27190.86, 1.19, '2026-10-02',
       { source: 'tencent', as_of: '2026-10-02 17:15:59' }),
    mk('.INX', '标普500 (.INX)', 7722.72, 0.73, '2026-10-02',
       { source: 'tencent', as_of: '2026-10-02 16:59:13' }),
  ],
  failed: [],
};

describe('外盘区块：逐卡日期 + 降级不冒充', () => {
  it('ok 态渲染 5 张卡', () => {
    render(<GlobalIndices block={OK} />);
    expect(screen.getAllByTestId('global-index-card')).toHaveLength(5);
    expect(screen.getByText('道琼斯 (.DJI)')).toBeInTheDocument();
    expect(screen.getByText('标普500 (.INX)')).toBeInTheDocument();
  });

  it('每张卡展示**自己**的 data_date（不同市场日期可不同）', () => {
    render(<GlobalIndices block={OK} />);
    // 4 项 10-02、纳指 09-30 ⇒ 两个日期都要出现，证明未共用同一 trade_date
    expect(screen.getAllByText('截至 10-02 收盘')).toHaveLength(4);
    expect(screen.getByText('截至 09-30 收盘')).toBeInTheDocument();
  });

  it('方向色走 t-up / t-down（涨红跌绿）', () => {
    render(<GlobalIndices block={OK} />);
    const up = screen.getByText('+1.23%');
    const down = screen.getByText('-0.45%');
    expect(up.className.split(/\s+/)).toContain('t-up');
    expect(down.className.split(/\s+/)).toContain('t-down');
  });

  it('降级态：5 张卡全渲染「—」，绝不用 0 冒充', () => {
    render(<GlobalIndices
      block={{ status: 'unavailable', reason: '外盘指数数据源暂不可用' }} />);
    expect(screen.getAllByTestId('global-index-card')).toHaveLength(5);
    // 每张卡 3 个「—」（收盘 / 涨跌 / 日期）⇒ ≥ 5 已足以证明"逐卡降级"
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(5);
    expect(screen.queryByText('0.00')).toBeNull();
    expect(screen.queryByText('+0.00%')).toBeNull();
  });

  it('部分失败（failed 非空）时仍渲染已成功项', () => {
    render(<GlobalIndices block={{
      status: 'ok',
      items: [mk('.DJI', '道琼斯 (.DJI)', 42000.0, 0.5, '2026-10-02')],
      failed: ['韩国KOSPI', '纳斯达克 (.IXIC):ConnectionError'],
    }} />);
    expect(screen.getAllByTestId('global-index-card')).toHaveLength(5);
    expect(screen.getByText('截至 10-02 收盘')).toBeInTheDocument();
    // 未成功的 4 张卡仍是「—」，不得被 0 填充
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(8);
  });

  it('加载中渲染骨架卡，不渲染数据卡', () => {
    render(<GlobalIndices block={null} loading />);
    expect(screen.queryAllByTestId('global-index-card')).toHaveLength(0);
  });

  it('页面文案不含「实时」', () => {
    const { container } = render(<GlobalIndices block={OK} />);
    expect(container.textContent ?? '').not.toContain('实时');
  });
});

describe('外盘区块：腾讯报价（带时间戳）vs 新浪收盘', () => {
  it('有 as_of 的卡展示源时间戳（10-02 17:00 更新）', () => {
    render(<GlobalIndices block={OK_MIXED} />);
    expect(screen.getByText('10-02 17:00 更新')).toBeInTheDocument();
    expect(screen.getByText('10-02 17:15 更新')).toBeInTheDocument();
    expect(screen.getByText('10-02 16:59 更新')).toBeInTheDocument();
  });

  it('无 as_of 的卡展示「截至 <日期> 收盘」（日经/KOSPI 恒为此态）', () => {
    render(<GlobalIndices block={OK_MIXED} />);
    expect(screen.getAllByText('截至 10-02 收盘')).toHaveLength(2);
    // 不得把"只有收盘"的项也标成带时间戳的报价
    expect(screen.queryByText('10-02 17:00 更新')).not.toBeNull();
    expect(screen.getAllByText(/更新$/)).toHaveLength(3);
  });

  it('来源如实披露在 title（腾讯行情 / 新浪财经）', () => {
    render(<GlobalIndices block={OK_MIXED} />);
    expect(screen.getByText('10-02 17:00 更新').getAttribute('title'))
      .toContain('腾讯行情');
    // 日经 + KOSPI 两张卡同为"收盘"文案 ⇒ 用 getAllByText 取首张
    expect(screen.getAllByText('截至 10-02 收盘')[0].getAttribute('title'))
      .toContain('新浪财经');
  });

  it('带时间戳的卡也不写「实时」（白天美股休市，那是上一收盘时刻）', () => {
    const { container } = render(<GlobalIndices block={OK_MIXED} />);
    expect(container.textContent ?? '').not.toContain('实时');
  });

  it('as_of 为 null（后端显式给空）等同"无时间戳"⇒ 走收盘文案', () => {
    render(<GlobalIndices block={{
      status: 'ok',
      items: [mk('.DJI', '道琼斯 (.DJI)', 42000.0, 0.5, '2026-10-02',
                 { source: 'sina', as_of: null })],
      failed: [],
    }} />);
    expect(screen.getByText('截至 10-02 收盘')).toBeInTheDocument();
  });

  it('as_of 时间戳短于 16 位时退化为月-日，不产出残缺文案', () => {
    render(<GlobalIndices block={{
      status: 'ok',
      items: [mk('.DJI', '道琼斯 (.DJI)', 42000.0, 0.5, '2026-10-02',
                 { source: 'tencent', as_of: '2026-10-02' })],
      failed: [],
    }} />);
    expect(screen.getByText('10-02 更新')).toBeInTheDocument();
  });
});
