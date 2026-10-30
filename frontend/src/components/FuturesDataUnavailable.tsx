interface Props {
  title?: string;
  reason: string;
}

export function FuturesDataUnavailable({
  title = '数据暂不可用',
  reason,
}: Props) {
  return (
    <div
      role="status"
      className="rounded-md border border-hair bg-surface-alt px-3.5 py-4 text-center"
    >
      <div className="text-xs font-semibold text-ink">{title}</div>
      <div className="mt-1 text-2xs leading-relaxed text-ink-muted">{reason}</div>
    </div>
  );
}
