import sys; sys.path.insert(0,".")
from app.api.v1.datacenter import _scan_dataset, DATASET_META
from app.core.config import get_settings
import json
s = get_settings()
print("DATA_ROOT:", s.DATA_ROOT)
for key in DATASET_META:
    p = s.DATA_ROOT / key
    meta = _scan_dataset(p, key)
    if meta is None:
        print(f"{key:22s} -> None (不存在或 0 行)")
    else:
        cov = (meta['total_files']-meta['skipped_files'])/meta['total_files'] if meta['total_files'] else 0
        print(f"{key:22s} rows={meta['rows']:>9} syms={meta['symbols']:>5} {meta['start']}~{meta['end']} skipped={meta['skipped_files']}/{meta['total_files']} cov={cov:.3f}")
