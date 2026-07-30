from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


class Tournament(BaseModel):
    id: str
    name: str
    source: str
    source_url: Optional[str] = None

    start_date: date
    end_date: Optional[date] = None
    registration_deadline: Optional[date] = None

    city: Optional[str] = None
    state: Optional[str] = None
    country: str = "India"
    venue: Optional[str] = None

    latitude: Optional[float] = None
    longitude: Optional[float] = None
    geo_precision: Optional[str] = None  # "city" | "state" | None

    format: Optional[str] = None  # classical | rapid | blitz | mixed
    time_control: Optional[str] = None
    rounds: Optional[int] = None

    is_fide_rated: bool = False
    is_aicf_rated: bool = False

    age_categories: list[str] = Field(default_factory=list)
    rating_categories: list[str] = Field(default_factory=list)
    open_to_all: bool = True

    entry_fee_inr: Optional[int] = None
    prize_fund_inr: Optional[int] = None
    expected_field_size: Optional[int] = None

    organizer: Optional[str] = None
    contact: Optional[str] = None
    registration_url: Optional[str] = None

    raw_text: Optional[str] = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Subscription(BaseModel):
    """Phase-2 stub — accept the W3C PushSubscription shape."""
    endpoint: str
    keys: dict[str, str]
    states_filter: list[str] = Field(default_factory=list)
    fide_only: bool = False
