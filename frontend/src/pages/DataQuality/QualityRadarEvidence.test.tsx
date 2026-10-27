import { render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import QualityRadarCard from './QualityRadarCard';
const h = vi.hoisted(() => ({ qualityRadar: vi.fn(), chart: vi.fn() }));
vi.mock('@/api/production', () => ({ opsApi: { qualityRadar: h.qualityRadar } }));
vi.mock('@/utils/useChart', () => ({ useChart: (option: unknown) => { h.chart(option); return { current: null }; } }));
beforeEach(() => vi.clearAllMocks());
it('缺三个维度证据时保留五维明细，不绘制补零雷达、不授予综合评级', async () => {
 h.qualityRadar.mockResolvedValue({ dataset: 'daily_bar', status: 'partial', composite_score: null, grade: null, summary: '只完成本地抽样检查', source: { scope: '本地抽样', files_scanned: 3, rows_scanned: 300, symbols_scanned: 3, symbols_available: 20, truncated: true, files_failed: 0 }, dimensions: ['完整性', '准确性', '一致性', '稳定性', '及时性'].map((name, index) => ({ key: name, name, score: index < 2 ? 96 : null, weight: 0.2, benchmark: 95, status: index < 2 ? 'PASS' : 'UNAVAILABLE', detail: index < 2 ? '真实样本' : '未执行检查' })) });
 render(<QualityRadarCard />);
 await screen.findByText('综合评级不可用');
 expect(screen.getByText('部分维度缺少证据，雷达图暂不可用')).toBeInTheDocument();
 expect(screen.getAllByText('不可用')).toHaveLength(4);
 expect(screen.queryByText(/AAA/)).toBeNull();
 expect(h.chart).toHaveBeenLastCalledWith(null);
 expect(screen.getByText(/300/)).toBeInTheDocument();
});
it('全部维度未知时显示未知状态，不把分数当零', async () => {
 h.qualityRadar.mockResolvedValue({ dataset: 'daily_bar', status: 'unavailable', composite_score: null, grade: null, summary: '数据目录缺失', dimensions: [{ key: 'x', name: '完整性', score: null, weight: 1, benchmark: 95, status: 'UNAVAILABLE', detail: '无样本' }] });
 render(<QualityRadarCard />);
 await screen.findByText('综合评级不可用');
 expect(screen.queryByText('0.0')).toBeNull();
 expect(screen.getByText('数据目录缺失')).toBeInTheDocument();
});
