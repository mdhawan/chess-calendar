"""ChessBase India adapter — pulls structured tournament data from their
calendar JSON API (POST /json/getTournaments/), which backs chessbase.in/calendar.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from . import Adapter
from ..models import Tournament

API_URL = "https://chessbase.in/json/getTournaments/"
REFERER = "https://chessbase.in/calendar"
USER_AGENT = "chess-calendar/0.1 (personal use)"
LOOKAHEAD_MONTHS = 13

# ISO-3 → display country (only ones we care to map; others pass through unchanged)
_COUNTRY_ISO3 = {"IND": "India"}


class ChessBaseIndiaAdapter(Adapter):
    name = "chessbase.in"

    def fetch(self) -> list[Tournament]:
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=30 * LOOKAHEAD_MONTHS)
        payload = {
            "Start": now.strftime("%Y-%m-%dT00:00:00.000Z"),
            "End": end.strftime("%Y-%m-%dT23:59:59.000Z"),
        }
        headers = {
            "User-Agent": USER_AGENT,
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": REFERER,
            "Accept": "application/json",
        }
        with httpx.Client(headers=headers, timeout=20.0, follow_redirects=True) as client:
            resp = client.post(API_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()

        if not isinstance(data, dict) or data.get("issuccess") is False:
            return []

        out: list[Tournament] = []
        for _month_key, items in data.items():
            if not isinstance(items, list):
                continue
            for item in items:
                t = _to_tournament(item, source=self.name)
                if t:
                    out.append(t)
        return out


def _to_tournament(item: dict, source: str) -> Optional[Tournament]:
    if not isinstance(item, dict):
        return None
    name = (item.get("title") or "").strip()
    start = _parse_iso(item.get("start"))
    if not name or not start:
        return None

    iso3 = (item.get("country") or "").strip().upper()
    country = _COUNTRY_ISO3.get(iso3, iso3 or "India")
    fmt = (item.get("type") or "").strip().lower() or None
    item_id = item.get("id") or hashlib.sha1(f"{name}|{start.isoformat()}".encode()).hexdigest()[:12]

    return Tournament(
        id="cbi-" + item_id,
        name=name,
        source=source,
        source_url=f"https://chessbase.in/tournament/{item_id}",
        start_date=start,
        end_date=_parse_iso(item.get("end")),
        city=(item.get("city") or None) or None,
        state=(item.get("state") or None) or None,
        country=country,
        format=fmt if fmt in {"classical", "rapid", "blitz", "mixed"} else None,
        organizer=item.get("organizername") or None,
        contact=item.get("organizeremail") or item.get("organizerphone") or None,
        registration_url=item.get("awsbrochureurl") or None,
        raw_text=item.get("desc") or None,
    )


def _parse_iso(s: Optional[str]):
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    except ValueError:
        return None
