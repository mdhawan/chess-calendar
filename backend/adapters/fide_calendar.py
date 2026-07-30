"""FIDE calendar — secondary source. Cross-checks chess-results.com."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from . import Adapter
from ..models import Tournament

URL = "https://calendar.fide.com/calendar?country=IND"
USER_AGENT = "chess-calendar/0.1 (personal use)"


class FideCalendarAdapter(Adapter):
    name = "fide.com/calendar"

    def fetch(self) -> list[Tournament]:
        with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20.0, follow_redirects=True) as client:
            resp = client.get(URL)
            resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        out: list[Tournament] = []
        # FIDE renders calendar entries inside .calendar-list-item / table rows;
        # we accept either layout and extract by labelled fields.
        for item in soup.select(".calendar-list-item, table.events tr"):
            name_el = item.select_one(".name, .title, a[href*='/event/']")
            if not name_el:
                continue
            name = name_el.get_text(strip=True)
            link = name_el.get("href") if name_el.name == "a" else None
            text = item.get_text(" ", strip=True)
            start, end = _extract_dates(text)
            if not start:
                continue
            out.append(
                Tournament(
                    id="fide-" + hashlib.sha1(f"{name}|{start}".encode()).hexdigest()[:12],
                    name=name,
                    source=self.name,
                    source_url=link,
                    start_date=start,
                    end_date=end,
                    country="India",
                    is_fide_rated=True,
                    raw_text=text,
                )
            )
        return out


def _extract_dates(text: str):
    m = re.search(r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})\s*(?:[-–to]+\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}))?", text)
    if not m:
        return None, None
    s = _parse(m.group(1))
    e = _parse(m.group(2)) if m.group(2) else s
    return s, e


def _parse(t: Optional[str]):
    if not t:
        return None
    for fmt in ("%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d.%m.%y"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            pass
    return None
