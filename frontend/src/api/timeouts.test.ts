/**
 * 超时集中表契约测试（A-6 / F-3，2026-10-02）。
 *
 * 守的是"表本身"与 `longTimeout` 的**运行时**保证：
 * 长超时路径未登记时必须**立即抛错**，而不是静默返回一个会被后端误杀的值。
 */
import { describe, expect, it } from 'vitest';

import { LONG_PATHS, MAX_TIMEOUT_MS, TIMEOUT, longTimeout } from './timeouts';

describe('TIMEOUT 集中表', () => {
  it('所有档位都是正数', () => {
    for (const [name, ms] of Object.entries(TIMEOUT)) {
      expect(ms, `档位 ${name} 非正数`).toBeGreaterThan(0);
    }
  });

  it('档位值互不重复（重复会让闸门的"按值豁免"产生歧义）', () => {
    const vals = Object.values(TIMEOUT);
    expect(new Set(vals).size).toBe(vals.length);
  });

  it('LONG_PATHS 的登记值必须来自 TIMEOUT（不得写裸数字）', () => {
    const tierValues = new Set<number>(Object.values(TIMEOUT));
    for (const [path, ms] of Object.entries(LONG_PATHS)) {
      expect(tierValues.has(ms), `${path} 的值 ${ms} 不在 TIMEOUT 表中`).toBe(true);
    }
  });

  it('MAX_TIMEOUT_MS 等于全表最大值', () => {
    expect(MAX_TIMEOUT_MS).toBe(Math.max(...Object.values(TIMEOUT)));
  });
});

describe('longTimeout：长超时路径必须显式登记', () => {
  it('已登记路径返回对应档位值', () => {
    expect(longTimeout('/api/v1/backtest/strategy-run')).toBe(TIMEOUT.optimize);
    expect(longTimeout('/api/v1/ops/dag/rerun')).toBe(TIMEOUT.rerun);
  });

  it('🔴 未登记路径立即抛错（绝不静默返回默认值）', () => {
    expect(() => longTimeout('/api/v1/studio/factors'))
      .toThrow(/未在 LONG_PATHS 登记/);
  });

  it('🔴 抛错信息必须说明后果（否则后人看不懂为什么要登记）', () => {
    expect(() => longTimeout('/api/v1/nope')).toThrow(/误杀/);
  });

  it('登记表非空（若被清空，C1/C2/C5 会同时失效）', () => {
    expect(Object.keys(LONG_PATHS).length).toBeGreaterThan(0);
  });
});
