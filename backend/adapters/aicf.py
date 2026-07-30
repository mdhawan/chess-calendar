"""AICF adapter — scrapes the structured events table at /all-events/.

The page renders a single HTML table with columns:
    Name of Tournament | Event Code | Start Date | End Date | Place | Brochure

This avoids any LLM/PDF-extraction cost. The brochure cell, when present,
links to the official poster PDF, which we surface as `registration_url` so the
UI can deep-link.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from . import Adapter
from ..models import Tournament

BASE = "https://aicf.in/"
EVENTS_URL = urljoin(BASE, "all-events/")
USER_AGENT = "chess-calendar/0.1 (personal use)"

# Indian state names found in the "Place" column. Order matters: longer names
# first so "West Bengal" wins over "Bengal".
INDIAN_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh",
    "Goa", "Gujarat", "Haryana", "Himachal Pradesh", "Jammu and Kashmir",
    "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra",
    "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab",
    "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura",
    "Uttar Pradesh", "Uttarakhand", "West Bengal",
    "Delhi", "Chandigarh", "Puducherry", "Ladakh",
]
# AICF sometimes abbreviates; map back to canonical.
STATE_ALIASES = {"UP": "Uttar Pradesh", "MP": "Madhya Pradesh", "TN": "Tamil Nadu",
                 "AP": "Andhra Pradesh", "WB": "West Bengal", "HP": "Himachal Pradesh",
                 "J&K": "Jammu and Kashmir", "CG": "Chhattisgarh"}


class AicfAdapter(Adapter):
    name = "aicf.in"

    def fetch(self) -> list[Tournament]:
        with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20.0, follow_redirects=True) as client:
            resp = client.get(EVENTS_URL)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        table = soup.find("table")
        if not table:
            return []

        out: list[Tournament] = []
        for tr in table.find_all("tr")[1:]:  # skip header
            cells = tr.find_all(["td", "th"])
            if len(cells) < 5:
                continue

            name = cells[0].get_text(" ", strip=True).replace("\xa0", " ")
            code = cells[1].get_text(" ", strip=True)
            start = _parse_dmy(cells[2].get_text(" ", strip=True))
            end = _parse_dmy(cells[3].get_text(" ", strip=True))
            place = cells[4].get_text(" ", strip=True)

            if not name or not start:
                continue

            brochure = None
            if len(cells) >= 6:
                a = cells[5].find("a", href=True)
                if a:
                    brochure = urljoin(BASE, a["href"])

            city, state = _split_place(place)
            seed = f"aicf|{code}|{name}|{start.isoformat()}"
            out.append(
                Tournament(
                    id="aicf-" + (code.replace("/", "-") or hashlib.sha1(seed.encode()).hexdigest()[:12]),
                    name=name,
                    source=self.name,
                    source_url=EVENTS_URL,
                    start_date=start,
                    end_date=end or start,
                    city=city,
                    state=state,
                    country="India",
                    format=_format_from_title(name),
                    is_fide_rated=_is_fide_rated(name),
                    is_aicf_rated=True,
                    registration_url=brochure,
                )
            )
        return out


def _parse_dmy(s: str) -> Optional[date]:
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _split_place(place: str) -> tuple[Optional[str], Optional[str]]:
    """Place is one of: "<state>", "<city>", "<city>, <state>"."""
    if not place:
        return None, None
    parts = [p.strip() for p in place.split(",") if p.strip()]
    state = None
    city = None
    if len(parts) == 2:
        # "City, State"
        city = parts[0]
        state = _canonical_state(parts[1]) or parts[1]
        return city, state
    # single token — could be either
    only = parts[0]
    canon = _canonical_state(only)
    if canon:
        return None, canon
    return only, None


def _canonical_state(token: str) -> Optional[str]:
    t = token.strip()
    if t in INDIAN_STATES:
        return t
    upper = t.upper()
    if upper in STATE_ALIASES:
        return STATE_ALIASES[upper]
    # case-insensitive match against full names
    for s in INDIAN_STATES:
        if s.lower() == t.lower():
            return s
    return None


def _format_from_title(title: str) -> Optional[str]:
    t = title.lower()
    if "blitz" in t:
        return "blitz"
    if "rapid" in t:
        return "rapid"
    if "classical" in t or "standard" in t:
        return "classical"
    return None


def _is_fide_rated(title: str) -> bool:
    t = title.lower()
    return "fide" in t and "non-fide" not in t and "non fide" not in t
