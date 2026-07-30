"""
chess-results.com adapter — primary data source.

The federation page `fed.aspx?fed=IND` is capped at 50 entries and dominated by
whichever states posted most recently. To get full coverage we iterate the
per-state subpages (`bdld1=1..31`), each of which lists up to 50 tournaments
for that state. State name is not on the page; we use a static map from the
search-form dropdown (`combo_bdld`).

For every tournament link we follow the detail panel (`tnr<id>.aspx?turdet=YES`)
which renders a clean key/value table containing the start date, venue,
organizer, rounds, and time control. The listing alone has none of these.

Speed: the ~31 state pages and the ~1 drilldown per tournament are fetched
concurrently through a bounded thread pool (`CHESS_RESULTS_WORKERS`, default
16), replacing the old per-request sleep. On top of that, detail pages resolved
on a prior refresh are reused from the DB (`cached_details`) instead of being
re-fetched, so a steady-state refresh is ~31 listing requests plus a drilldown
only for genuinely new tournaments. A "fast" mode skips drilldowns entirely.

IMPORTANT: chess-results caps each IP at 2000 requests/day (it serves a plain
"limit exceeded" page — HTTP 200, no tournament links — once you're over). A
cold full crawl is ~1500 requests, so it fits in one day's budget but leaves
little room; do NOT raise the worker count to "go faster" — the daily cap, not
concurrency, is the ceiling, and the detail cache is what keeps us under it.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from . import Adapter
from ..db import cached_details
from ..models import Tournament

BASE = "https://chess-results.com/"
USER_AGENT = "chess-calendar/0.1 (personal use)"
TIMEOUT = httpx.Timeout(20.0, connect=10.0)
# Concurrency is the throughput knob now: we keep at most this many connections
# open at once instead of sleeping between serial requests. It is NOT worth
# raising much higher — chess-results caps each IP at 2000 requests/day, so the
# daily budget, not thread count, is the real ceiling. 16 saturates a full crawl
# comfortably within that budget. Tune via env var.
MAX_WORKERS = int(os.environ.get("CHESS_RESULTS_WORKERS", "16"))
SLEEP = float(os.environ.get("CHESS_RESULTS_SLEEP", "0"))  # optional per-request delay

# bdld1 → Indian state/UT name (sourced from chess-results' own combo_bdld dropdown)
BDLD_STATES: dict[int, str] = {
    1: "Andhra Pradesh", 2: "Arunachal Pradesh", 3: "Assam", 4: "Bihar",
    5: "Chhattisgarh", 6: "Goa", 7: "Gujarat", 8: "Haryana",
    9: "Himachal Pradesh", 10: "Jammu and Kashmir", 11: "Jharkhand",
    12: "Karnataka", 13: "Kerala", 14: "Madhya Pradesh", 15: "Maharashtra",
    16: "Manipur", 17: "Meghalaya", 18: "Mizoram", 19: "Nagaland",
    20: "Odisha", 21: "Punjab", 22: "Rajasthan", 23: "Sikkim",
    24: "Tamil Nadu", 25: "Telangana", 26: "Tripura", 27: "Uttar Pradesh",
    28: "Uttarakhand", 29: "West Bengal", 30: "Union territories", 31: "Other",
}

FORMAT_PREFIX = {
    "rp": "rapid",
    "bz": "blitz",
    "st": "classical",
    "kp": "classical",  # K.O. tournaments — rare
}


def _client() -> httpx.Client:
    # A single Client is shared across the thread pool. httpx.Client is
    # thread-safe for issuing requests; size the connection pool to the worker
    # count so concurrent drilldowns don't queue on a too-small pool.
    limits = httpx.Limits(
        max_connections=MAX_WORKERS,
        max_keepalive_connections=MAX_WORKERS,
    )
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
        timeout=TIMEOUT,
        follow_redirects=True,
        limits=limits,
    )


def _parse_date(s: str) -> Optional[date]:
    s = (s or "").strip()
    if not s:
        return None
    # chess-results uses "YYYY/MM/DD" on the detail panel.
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # "Date 2026/05/31 to 2026/06/02"
    m = re.search(r"(\d{4}[/-]\d{1,2}[/-]\d{1,2})", s)
    if m:
        try:
            return datetime.strptime(m.group(1).replace("-", "/"), "%Y/%m/%d").date()
        except ValueError:
            pass
    return None


def _parse_date_range(s: str) -> tuple[Optional[date], Optional[date]]:
    s = (s or "").strip()
    if not s:
        return None, None
    parts = re.split(r"\s+to\s+|\s*[–-]\s*", s)
    parts = [p for p in parts if p]
    if not parts:
        return None, None
    if len(parts) == 1:
        d = _parse_date(parts[0])
        return d, d
    return _parse_date(parts[0]), _parse_date(parts[-1])


def _format_from_prefix(text: str) -> Optional[str]:
    # Listing rows show e.g. "Rp 1 Days" or "St 5 Hours" in the third column.
    m = re.match(r"\s*(Rp|Bz|St|Kp)\b", text or "", re.I)
    if not m:
        return None
    return FORMAT_PREFIX.get(m.group(1).lower())


def _format_from_label(text: str) -> Optional[str]:
    n = (text or "").lower()
    if "blitz" in n:
        return "blitz"
    if "rapid" in n:
        return "rapid"
    if "classical" in n or "standard" in n:
        return "classical"
    return None


def _id_for(tnr_id: str) -> str:
    return f"cr-{tnr_id}"


def _extract_tnr_id(href: str) -> Optional[str]:
    m = re.search(r"tnr(\d+)\.aspx", href or "")
    return m.group(1) if m else None


def _is_finished_listing_row(third_cell: str) -> bool:
    """The 3rd cell on a listing row contains "Last update <relative time>".
    A tournament that's already finished still shows here; we keep all rows and
    let the detail-panel date filter decide.
    """
    return False  # accept everything; downstream filters by date


class ChessResultsAdapter(Adapter):
    name = "chess-results.com"

    def __init__(self, fast: bool = False):
        # When fast=True we skip drill-downs and emit only tournaments where
        # the title contains a parseable date. Used for first-launch feedback.
        self.fast = fast or os.environ.get("CHESS_RESULTS_FAST") == "1"
        # id -> cached detail dict from a prior refresh; populated in fetch().
        self._cache: dict[str, dict] = {}

    def fetch(self) -> list[Tournament]:
        # Detail pages already resolved on a prior refresh — reused instead of
        # re-drilling, which is what keeps us under the 2000 req/day IP cap and
        # makes steady-state refreshes fast (only new tournaments get a fetch).
        self._cache = cached_details(self.name)
        states = list(BDLD_STATES.items())
        with _client() as client, ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            # Phase 1: fetch all per-state listing pages concurrently.
            def _listing(item: tuple[int, str]) -> tuple[str, list[dict]]:
                bdld, state_name = item
                url = f"{BASE}fed.aspx?lan=1&fed=IND&bdld1={bdld}"
                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                except Exception as e:
                    print(f"      {state_name}: list fetch failed ({e})", flush=True)
                    return state_name, []
                return state_name, self._parse_listing(resp.text)

            # Dedup across states, keeping the first (state, row) we see per id.
            seen: set[str] = set()
            jobs: list[tuple[dict, str]] = []
            for state_name, rows in pool.map(_listing, states):
                for row in rows:
                    tnr_id = row["tnr_id"]
                    if tnr_id in seen:
                        continue
                    seen.add(tnr_id)
                    jobs.append((row, state_name))
            print(f"      listings done: {len(jobs)} unique tournaments to resolve", flush=True)

            # Phase 2: build each tournament (drilldown fetch) concurrently.
            def _build(job: tuple[dict, str]) -> Optional[Tournament]:
                row, state_name = job
                return self._build_tournament(client, row, state_name)

            out = [t for t in pool.map(_build, jobs) if t is not None]
        print(f"      resolved {len(out)} tournaments", flush=True)
        return out

    def _parse_listing(self, html: str) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")
        result = []
        for a in soup.find_all("a", href=re.compile(r"tnr\d+\.aspx")):
            tnr_id = _extract_tnr_id(a.get("href", ""))
            if not tnr_id:
                continue
            tr = a.find_parent("tr")
            cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")] if tr else []
            third = cells[2] if len(cells) > 2 else ""
            result.append(
                {
                    "tnr_id": tnr_id,
                    "name": a.get_text(" ", strip=True),
                    "url": a.get("href"),
                    "third_cell": third,
                }
            )
        return result

    def _build_tournament(
        self, client: httpx.Client, row: dict, state_name: str
    ) -> Optional[Tournament]:
        tnr_id = row["tnr_id"]
        name = row["name"]
        url = row["url"]
        if not name or not tnr_id:
            return None

        # Skip the expensive detail drill-down when the title alone reveals a
        # clearly-past date. This is the bulk of the crawl cost (~0.6s/request),
        # and past events are dropped downstream anyway. A 14-day grace window
        # guards against a title date being an early round / off-by-a-few-days,
        # so anything borderline still gets fetched and precisely filtered.
        title_date = _date_from_title(name)
        if title_date and title_date < date.today() - timedelta(days=14):
            return None

        details = {}
        if not self.fast:
            cached = self._cache.get(_id_for(tnr_id))
            details = _details_from_cache(cached) if cached is not None else None
            if details is None:
                # Not cached, or the cached row failed validation — fetch fresh
                # rather than trust it. Spends one request from the daily budget.
                details = self._fetch_details(client, tnr_id)

        start_date = details.get("start_date")
        end_date = details.get("end_date")

        # Title fallback: chess-results convention is "<Month Day, City, Name>".
        if not start_date:
            start_date = _date_from_title(name)
            end_date = end_date or start_date

        if not start_date:
            return None

        fmt = (
            details.get("format")
            or _format_from_prefix(row["third_cell"])
            or _format_from_label(name)
        )

        return Tournament(
            id=_id_for(tnr_id),
            name=name,
            source=self.name,
            source_url=url,
            start_date=start_date,
            end_date=end_date or start_date,
            country="India",
            state=state_name if state_name not in ("Other", "Union territories") else None,
            city=details.get("city"),
            venue=details.get("venue"),
            format=fmt,
            time_control=details.get("time_control"),
            rounds=details.get("rounds"),
            organizer=details.get("organizer"),
            is_fide_rated=details.get("is_fide_rated", True),
            raw_text=row["third_cell"],
        )

    def _fetch_details(self, client: httpx.Client, tnr_id: str) -> dict:
        url = f"{BASE}tnr{tnr_id}.aspx?lan=1&turdet=YES"
        try:
            if SLEEP:
                time.sleep(SLEEP)
            resp = client.get(url)
            resp.raise_for_status()
        except Exception:
            return {}
        return _parse_details(resp.text)


def _date_from_title(title: str) -> Optional[date]:
    """Chess-results titles often start with 'May 31, Lucknow, ...' or include
    'Date - 24.05.2026' or '28 June 2026'. Try a handful of patterns,
    falling back to month+day with assumed current/next year."""
    if not title:
        return None
    months = "January|February|March|April|May|June|July|August|September|October|November|December"
    full_year_pats = [
        (rf"\b({months})\s+(\d{{1,2}})(?:[a-z]+)?,?\s+(\d{{4}})\b", "%B %d %Y"),
        (rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({months})\s+(\d{{4}})\b", "%d %B %Y"),
        (r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b", "%d.%m.%Y"),
        (r"\b(\d{4})[./-](\d{1,2})[./-](\d{1,2})\b", "%Y.%m.%d"),
    ]
    today = datetime.now().date()
    year = today.year

    for pat, _fmt in full_year_pats:
        m = re.search(pat, title, re.I)
        if not m:
            continue
        s = m.group(0)
        normalized = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", s, flags=re.I).replace(",", "").replace("/", ".").replace("-", ".")
        for try_fmt in ("%B %d %Y", "%d %B %Y", "%d.%m.%Y", "%Y.%m.%d"):
            try:
                return datetime.strptime(normalized, try_fmt).date()
            except ValueError:
                continue

    # Month+day-only pattern, e.g. "May 31, Lucknow, ..."
    m = re.search(rf"\b({months})\s+(\d{{1,2}})\b", title, re.I)
    if m:
        s = f"{m.group(1)} {m.group(2)} {year}"
        try:
            d = datetime.strptime(s, "%B %d %Y").date()
            if d < today:
                d = datetime.strptime(f"{m.group(1)} {m.group(2)} {year + 1}", "%B %d %Y").date()
            return d
        except ValueError:
            pass
    return None


_VALID_FORMATS = {"rapid", "blitz", "classical"}


def _details_from_cache(row: dict) -> Optional[dict]:
    """Rebuild the detail dict from a cached DB row, or None if it's untrustworthy.

    Returns None (forcing a fresh fetch) when the row fails a sanity check — this
    guards against reusing rows written by the pre-fix column-misalignment bug
    (which left e.g. a latitude in ``format`` and a timestamp in ``rounds``).
    """
    s = _parse_date(row.get("start_date") or "")
    if not s:
        return None
    # A genuine drilled row always stores is_fide_rated (the builder defaults it
    # to True), so NULL here marks a repaired/incomplete row — reject it so it
    # re-drills once and restores format/rounds/time_control.
    if row.get("is_fide_rated") is None:
        return None
    # `rounds` must be an int; the corruption stored strings like "state" there.
    rounds = row.get("rounds")
    if rounds is not None and not isinstance(rounds, int):
        return None
    # `format` is a small enum; corruption stored floats like "26.2006" there.
    fmt = row.get("format")
    if fmt is not None and fmt not in _VALID_FORMATS:
        return None

    out: dict = {"start_date": s, "end_date": _parse_date(row.get("end_date") or "") or s}
    for k in ("city", "venue", "format", "time_control", "organizer"):
        if row.get(k) is not None:
            out[k] = row[k]
    if rounds is not None:
        out["rounds"] = rounds
    if row.get("is_fide_rated") is not None:
        out["is_fide_rated"] = bool(row["is_fide_rated"])
    return out


def _parse_details(html: str) -> dict:
    """Pull the key/value detail rows from the turdet=YES panel."""
    soup = BeautifulSoup(html, "html.parser")
    kv: dict[str, str] = {}
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) != 2:
            continue
        k = cells[0].get_text(" ", strip=True)
        v = cells[1].get_text(" ", strip=True)
        if k and v and len(k) < 60:
            kv[k.lower()] = v

    out: dict = {}
    date_text = kv.get("date") or ""
    s, e = _parse_date_range(date_text)
    if s:
        out["start_date"] = s
        out["end_date"] = e

    # Time control label ("Time control (Rapid)") encodes format
    for k, v in kv.items():
        if k.startswith("time control"):
            out["time_control"] = v
            fmt = _format_from_label(k)
            if fmt:
                out["format"] = fmt
            break

    if "location" in kv:
        loc = kv["location"]
        out["venue"] = loc
        # Heuristic: city is the last comma-separated token
        parts = [p.strip() for p in loc.split(",") if p.strip()]
        if parts:
            out["city"] = parts[-1]

    if "number of rounds" in kv:
        try:
            out["rounds"] = int(re.search(r"\d+", kv["number of rounds"]).group(0))
        except (AttributeError, ValueError):
            pass

    if "organizer(s)" in kv:
        out["organizer"] = kv["organizer(s)"]
    elif "organizer" in kv:
        out["organizer"] = kv["organizer"]

    if "rating calculation" in kv:
        rc = kv["rating calculation"].lower()
        # "-" means no rating; anything else (e.g. "Rapid", "FIDE") is rated.
        out["is_fide_rated"] = rc not in ("-", "", "no", "unrated")

    return out
