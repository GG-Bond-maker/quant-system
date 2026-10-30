import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { futuresApi } from '@/api/futures';
import { useFuturesCatalog } from './useFuturesCatalog';

vi.mock('@/api/futures', () => ({
  futuresApi: {
    instruments: vi.fn(),
  },
}));

const instruments = [
  {
    code: 'AG',
    name: '沪银',
    tier: 'core',
    category: 'precious',
    trade_enabled: true,
  },
  {
    code: 'CF',
    name: '棉花',
    tier: 'core',
    category: 'agri',
    trade_enabled: true,
  },
  {
    code: 'IF',
    name: '沪深300股指期货',
    tier: 'core',
    category: 'index',
    trade_enabled: true,
  },
  {
    code: 'PB',
    name: '沪铅',
    tier: 'extended',
    category: 'metal',
    trade_enabled: false,
  },
] as never[];

describe('useFuturesCatalog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(futuresApi.instruments).mockResolvedValue({
      count: instruments.length,
      items: instruments,
    });
  });

  it('loads the full catalog and exposes sorted core instruments', async () => {
    const { result } = renderHook(() => useFuturesCatalog());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(futuresApi.instruments).toHaveBeenCalledTimes(1);
    expect(futuresApi.instruments).toHaveBeenCalledWith({ tier: 'all' });
    expect(result.current.items).toHaveLength(4);
    expect(result.current.coreItems.map((item) => item.code)).toEqual([
      'IF',
      'AG',
      'CF',
    ]);
    expect(result.current.error).toBeNull();
  });

  it('reloads the catalog on demand', async () => {
    const { result } = renderHook(() => useFuturesCatalog());

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    act(() => {
      result.current.reload();
    });

    await waitFor(() => {
      expect(futuresApi.instruments).toHaveBeenCalledTimes(2);
      expect(result.current.loading).toBe(false);
    });
  });

  it('does not request the catalog while disabled', async () => {
    const { result } = renderHook(() => useFuturesCatalog({ enabled: false }));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    expect(futuresApi.instruments).not.toHaveBeenCalled();
    expect(result.current.items).toEqual([]);
    expect(result.current.coreItems).toEqual([]);
    expect(result.current.error).toBeNull();
  });
});
