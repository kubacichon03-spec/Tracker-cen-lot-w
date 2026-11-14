from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from .models import Query


def load_config(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    _validate(cfg)
    return cfg


def _validate(cfg: dict[str, Any]) -> None:
    for key in ("origins", "destinations", "departure_dates", "trip"):
        if not cfg.get(key):
            raise ValueError(f"config.yaml: brak sekcji '{key}'")
    if cfg["trip"].get("type") not in ("round_trip", "one_way"):
        raise ValueError("config.yaml: trip.type musi być round_trip albo one_way")


def _as_date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def departure_dates(cfg: dict[str, Any]) -> list[date]:
    """departure_dates może być listą dat albo zakresem {from, to, step_days}."""
    raw = cfg["departure_dates"]
    if isinstance(raw, dict):
        start, end = _as_date(raw["from"]), _as_date(raw["to"])
        step = int(raw.get("step_days", 1))
        out, d = [], start
        while d <= end:
            out.append(d)
            d += timedelta(days=step)
        if raw.get("include_last", True) and out[-1] != end:
            out.append(end)
        return out
    return [_as_date(x) for x in raw]


def build_queries(cfg: dict[str, Any], today: date | None = None) -> list[Query]:
    """Jedno zapytanie = grupa celów x data wylotu x długość pobytu.
    Daty z przeszłości są pomijane."""
    today = today or date.today()
    origins = tuple(cfg["origins"].keys())
    round_trip = cfg["trip"]["type"] == "round_trip"
    stays = cfg["trip"].get("stay_days") or [14]
    return_to = cfg["trip"].get("return_to") if round_trip else None

    queries: list[Query] = []
    for out in departure_dates(cfg):
        if out <= today:
            continue
        for group, dests in cfg["destinations"].items():
            if round_trip:
                for stay in stays:
                    queries.append(Query(group, origins, tuple(dests), out,
                                         out + timedelta(days=int(stay)), return_to))
            else:
                queries.append(Query(group, origins, tuple(dests), out, None))
    return queries
