"""Run all adapters; tolerant of any single source failing.

Adapters are independent (each hits a different site / file) so we run them
concurrently — the whole refresh takes as long as the slowest single source
rather than the sum of all of them.
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor


from .adapters import all_adapters
from .db import init_db


def _log(msg: str) -> None:
    print(f"[refresh] {msg}", flush=True)


def _run_one(adapter) -> dict:
    _log(f"  → {adapter.name} …")
    t0 = time.time()
    rows, error = adapter.run()
    dt = time.time() - t0
    if error:
        _log(f"    ✗ {adapter.name} failed in {dt:.1f}s: {error[:200]}")
    else:
        _log(f"    ✓ {adapter.name}: {rows} rows in {dt:.1f}s")
    return {"source": adapter.name, "rows": rows, "error": error}


def refresh_all() -> dict:
    init_db()
    summary: dict = {"sources": [], "total": 0}
    adapters = list(all_adapters())
    _log(f"starting refresh across {len(adapters)} sources (parallel)")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=len(adapters)) as pool:
        # map preserves input order, so `sources` stays deterministic.
        results = list(pool.map(_run_one, adapters))
    summary["sources"] = results
    summary["total"] = sum(r["rows"] for r in results)
    _log(f"done. total rows: {summary['total']} in {time.time() - t0:.1f}s")
    return summary


if __name__ == "__main__":
    import json
    print(json.dumps(refresh_all(), indent=2, default=str))
