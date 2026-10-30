import { useCallback, useEffect, useMemo, useState } from 'react';
import { futuresApi } from '@/api/futures';
import type { FuturesInstrumentInfo } from '@/types/futures';

const CATEGORY_ORDER = [
  'index',
  'bond',
  'precious',
  'metal',
  'ferrous',
  'energy',
  'agri',
];

interface FuturesCatalogState {
  items: FuturesInstrumentInfo[];
  coreItems: FuturesInstrumentInfo[];
  loading: boolean;
  error: string | null;
  reload: () => void;
}

interface FuturesCatalogOptions {
  enabled?: boolean;
}

function sortCatalog(items: FuturesInstrumentInfo[]): FuturesInstrumentInfo[] {
  return [...items].sort((a, b) => {
    const categoryDelta =
      CATEGORY_ORDER.indexOf(a.category) - CATEGORY_ORDER.indexOf(b.category);
    return categoryDelta || a.code.localeCompare(b.code);
  });
}

export function useFuturesCatalog({
  enabled = true,
}: FuturesCatalogOptions = {}): FuturesCatalogState {
  const [items, setItems] = useState<FuturesInstrumentInfo[]>([]);
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (!enabled) {
      setItems([]);
      setLoading(false);
      setError(null);
      return undefined;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    futuresApi.instruments({ tier: 'all' })
      .then((catalog) => {
        if (!cancelled) setItems(sortCatalog(catalog.items));
      })
      .catch((reason) => {
        if (!cancelled) {
          setItems([]);
          setError(reason instanceof Error ? reason.message : '期货品种目录加载失败');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [enabled, reloadKey]);

  const reload = useCallback(() => {
    setReloadKey((value) => value + 1);
  }, []);

  const coreItems = useMemo(
    () => items.filter((item) => item.tier === 'core'),
    [items],
  );

  return { items, coreItems, loading, error, reload };
}
