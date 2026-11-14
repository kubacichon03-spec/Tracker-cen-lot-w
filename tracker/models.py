from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Query:
    dest_group: str
    origins: tuple[str, ...]
    destinations: tuple[str, ...]
    outbound_date: date
    return_date: date | None
    return_to: str | None = None   # np. "KRK": powrót zawsze na to lotnisko (bilet multi-city)


@dataclass
class Offer:
    dest_group: str
    origin: str
    destination: str
    outbound_date: date
    return_date: date | None
    price: float
    currency: str
    airlines: str
    flight_numbers: str
    stops: int
    total_duration_min: int | None
    departure_time: str | None = None
    arrival_time: str | None = None
    rank: int | None = None          # 1..top_n jeśli wybrana do top, inaczej None
    effective_price: float | None = None
    return_to: str | None = None


@dataclass
class Insights:
    dest_group: str
    outbound_date: date
    return_date: date | None
    lowest_price: float | None
    price_level: str | None
    typical_low: float | None
    typical_high: float | None


@dataclass
class QueryResult:
    query: Query
    offers: list[Offer] = field(default_factory=list)
    insights: Insights | None = None
    error: str | None = None
