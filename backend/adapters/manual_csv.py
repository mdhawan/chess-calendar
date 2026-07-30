"""Manual CSV ingest — operator escape hatch for any source the scrapers miss.

Drop a CSV file into backend/data/manual/ with these headers:
  name, source, start_date, end_date, city, state, format,
  is_fide_rated, is_aicf_rated, registration_url, organizer, contact

Dates: YYYY-MM-DD. Booleans: true/false.
"""
from __future__ import annotations

import csv
import hashlib
from datetime import datetime
from pathlib import Path

from . import Adapter
from ..models import Tournament

DROP_DIR = Path(__file__).parent.parent / "data" / "manual"


def _bool(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "y")


def _date(v: str):
    v = (v or "").strip()
    if not v:
        return None
    return datetime.strptime(v, "%Y-%m-%d").date()


class ManualCsvAdapter(Adapter):
    name = "manual"

    def fetch(self) -> list[Tournament]:
        DROP_DIR.mkdir(parents=True, exist_ok=True)
        out: list[Tournament] = []
        for path in sorted(DROP_DIR.glob("*.csv")):
            with path.open(newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    name = row.get("name", "").strip()
                    start = _date(row.get("start_date", ""))
                    if not name or not start:
                        continue
                    src = row.get("source") or f"manual:{path.name}"
                    seed = f"{src}|{name}|{start.isoformat()}"
                    out.append(
                        Tournament(
                            id="man-" + hashlib.sha1(seed.encode()).hexdigest()[:12],
                            name=name,
                            source=src,
                            start_date=start,
                            end_date=_date(row.get("end_date", "")),
                            city=row.get("city") or None,
                            state=row.get("state") or None,
                            country=row.get("country") or "India",
                            format=row.get("format") or None,
                            is_fide_rated=_bool(row.get("is_fide_rated", "")),
                            is_aicf_rated=_bool(row.get("is_aicf_rated", "")),
                            registration_url=row.get("registration_url") or None,
                            organizer=row.get("organizer") or None,
                            contact=row.get("contact") or None,
                        )
                    )
        return out
