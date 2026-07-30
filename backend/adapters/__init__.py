from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Iterable

from .. import geo
from ..db import log_refresh, upsert_tournaments
from ..models import Tournament


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _enrich(rows: list[Tournament]) -> list[Tournament]:
    """Canonicalise state + attach coordinates, dropping non-Indian and past rows.

    Runs for every adapter so all sources benefit from one implementation and
    the DB never stores foreign / un-geocodable-state pollution or finished
    events (past tournaments are neither displayed nor worth persisting).
    """
    today = date.today()
    out: list[Tournament] = []
    for t in rows:
        if (t.country or "India") != "India":
            continue  # e.g. chessbase.in ROU/PRT/HUN entries
        if (t.end_date or t.start_date) < today:
            continue  # finished event — don't store or serve it
        canon = geo.canonical_state(t.state, t.city)
        if canon:
            t.state = canon
        lat, lng, precision = geo.geocode(t.city, t.state)
        t.latitude, t.longitude, t.geo_precision = lat, lng, precision
        out.append(t)
    return out


class Adapter:
    name: str = "base"

    def fetch(self) -> list[Tournament]:
        raise NotImplementedError

    def run(self) -> tuple[int, str | None]:
        started = _now()
        try:
            rows = _enrich(self.fetch())
            upsert_tournaments(rows)
            log_refresh(self.name, started, _now(), len(rows), None)
            return len(rows), None
        except Exception as e:  # one bad source must not break the pipeline
            log_refresh(self.name, started, _now(), 0, str(e))
            return 0, str(e)


def all_adapters() -> Iterable[Adapter]:
    from .chess_results import ChessResultsAdapter
    from .fide_calendar import FideCalendarAdapter
    from .aicf import AicfAdapter
    from .chessbase_india import ChessBaseIndiaAdapter
    from .manual_csv import ManualCsvAdapter

    return [
        ChessResultsAdapter(),
        FideCalendarAdapter(),
        AicfAdapter(),
        ChessBaseIndiaAdapter(),
        ManualCsvAdapter(),
    ]
