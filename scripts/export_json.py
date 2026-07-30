"""Refresh all sources, then export the served JSON as a static file.

This is the GitHub-Actions replacement for the live FastAPI server. It reuses
the backend's own ``refresh_all`` and ``query_tournaments`` verbatim, so the
scraping/geocoding/dedup logic is identical to local dev — only the delivery
changes: instead of answering ``/api/tournaments`` per request, we write the
exact same payload to ``site/tournaments.json`` for GitHub Pages to serve.

The SQLite DB (backend/data/chess.sqlite) is committed to the repo, so the
per-tournament detail cache survives between runs and a scheduled refresh stays
a handful of requests rather than a ~1500-request cold crawl — essential for
staying under chess-results' 2000 requests/IP/day cap across two runs a day.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Make the copied backend importable when run as `python scripts/export_json.py`.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.db import latest_refresh, query_tournaments  # noqa: E402
from backend.refresh import refresh_all  # noqa: E402

OUT = ROOT / "site" / "tournaments.json"


def main() -> int:
    summary = refresh_all()

    rows = query_tournaments()  # same defaults as GET /api/tournaments
    payload = {
        "count": len(rows),
        "tournaments": rows,
        "last_refresh": latest_refresh(),
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    # Deterministic key order + trailing newline keep the git diff minimal when
    # nothing meaningful changed between runs.
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )

    ok = sum(1 for s in summary["sources"] if not s["error"])
    print(f"[export] wrote {len(rows)} tournaments to {OUT} ({ok}/{len(summary['sources'])} sources ok)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
