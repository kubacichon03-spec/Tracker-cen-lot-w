"""Źródła cen. Każdy provider ma metodę search(query, cfg) -> QueryResult."""
from __future__ import annotations

import json
import os
import random
import re
import time
from datetime import date

import requests

from .models import Insights, Offer, Query, QueryResult


# ---------------------------------------------------------------- SerpApi ---

class SerpApiProvider:
    """Google Flights przez SerpApi. Jedno zapytanie obejmuje wszystkie lotniska
    wylotu i wszystkie lotniska docelowe z grupy (kody po przecinku)."""

    URL = "https://serpapi.com/search.json"

    def __init__(self, api_key: str | None = None, session: requests.Session | None = None):
        self.api_key = api_key or os.environ.get("SERPAPI_KEY")
        if not self.api_key:
            raise RuntimeError("Brak klucza SerpApi – ustaw zmienną środowiskową SERPAPI_KEY")
        self.session = session or requests.Session()

    def params(self, q: Query, cfg: dict) -> dict:
        p = {
            "engine": "google_flights",
            "currency": cfg.get("currency", "PLN"),
            "hl": cfg.get("language", "pl"),
            "gl": cfg.get("country", "pl"),
            "adults": cfg.get("adults", 1),
            "api_key": self.api_key,
        }
        if q.return_date and q.return_to:
            # bilet multi-city: tam z dowolnego lotniska, powrót zawsze do return_to
            p["type"] = "3"
            p["multi_city_json"] = json.dumps([
                {"departure_id": ",".join(q.origins), "arrival_id": ",".join(q.destinations),
                 "date": q.outbound_date.isoformat()},
                {"departure_id": ",".join(q.destinations), "arrival_id": q.return_to,
                 "date": q.return_date.isoformat()},
            ])
            return p
        p.update({
            "departure_id": ",".join(q.origins),
            "arrival_id": ",".join(q.destinations),
            "outbound_date": q.outbound_date.isoformat(),
            "type": "1" if q.return_date else "2",
        })
        if q.return_date:
            p["return_date"] = q.return_date.isoformat()
        return p

    def search(self, q: Query, cfg: dict) -> QueryResult:
        last_err = None
        for attempt in range(3):
            try:
                r = self.session.get(self.URL, params=self.params(q, cfg), timeout=90)
                if r.status_code in (429, 500, 502, 503, 504):
                    last_err = f"HTTP {r.status_code}"
                    time.sleep(5 * (attempt + 1))
                    continue
                r.raise_for_status()
                data = r.json()
                if data.get("error"):
                    # np. "Google Flights hasn't returned any results" – to nie awaria
                    return QueryResult(q, error=str(data["error"]))
                return parse_serpapi(data, q, cfg.get("currency", "PLN"))
            except (requests.RequestException, ValueError) as e:
                last_err = str(e)
                time.sleep(5 * (attempt + 1))
        return QueryResult(q, error=last_err)


def _parse_dt(s: str | None) -> str | None:
    return s or None


def parse_serpapi(data: dict, q: Query, currency: str) -> QueryResult:
    offers: list[Offer] = []
    for item in (data.get("best_flights") or []) + (data.get("other_flights") or []):
        legs = item.get("flights") or []
        price = item.get("price")
        if not legs or price is None:
            continue
        airlines = sorted({l.get("airline", "?") for l in legs})
        offers.append(Offer(
            dest_group=q.dest_group,
            origin=legs[0]["departure_airport"]["id"],
            destination=legs[-1]["arrival_airport"]["id"],
            outbound_date=q.outbound_date,
            return_date=q.return_date,
            price=float(price),
            currency=currency,
            airlines=", ".join(airlines),
            flight_numbers=" > ".join(l.get("flight_number", "?") for l in legs),
            stops=len(legs) - 1,
            total_duration_min=item.get("total_duration"),
            departure_time=_parse_dt(legs[0]["departure_airport"].get("time")),
            arrival_time=_parse_dt(legs[-1]["arrival_airport"].get("time")),
            return_to=q.return_to,
        ))

    ins = None
    pi = data.get("price_insights")
    if pi:
        rng = pi.get("typical_price_range") or [None, None]
        ins = Insights(q.dest_group, q.outbound_date, q.return_date,
                       pi.get("lowest_price"), pi.get("price_level"), rng[0], rng[1])
    return QueryResult(q, offers=offers, insights=ins)


# ------------------------------------------------------ fast-flights (free) ---

class FastFlightsProvider:
    """Darmowy: biblioteka `faster-flights` (import: fast_flights) czyta Google Flights
    bezpośrednio. Nie obsługuje wielu lotnisk w jednym zapytaniu, więc pyta o każdą
    parę lotnisk osobno. Dla podróży w obie strony zawsze używa biletu multi-city
    (tam: origin→cel, powrót: cel→return_to), żeby cena była łączna za cały bilet.

    Zabezpieczenia przed blokadą: losowa przerwa między zapytaniami, a po wykryciu
    blokady minuta przerwy i jedna ponowna próba; dwie blokady z rzędu przerywają
    sprawdzenie (żeby nie dobijać się do Google)."""

    def __init__(self, delay: tuple[float, float] = (3, 6), sleep=time.sleep):
        try:
            import fast_flights  # noqa: F401
        except ImportError as e:
            raise RuntimeError("Zainstaluj: pip install faster-flights") from e
        if not hasattr(fast_flights, "create_query"):
            raise RuntimeError("Za stara wersja – zainstaluj: pip install -U faster-flights")
        self.delay = delay
        self.sleep = sleep
        self.blocked_streak = 0

    def _fetch(self, legs, trip, cfg):
        from fast_flights import Passengers, create_query, get_flights
        query = create_query(
            flights=legs, trip=trip, seat="economy",
            passengers=Passengers(adults=cfg.get("adults", 1)),
            language=cfg.get("language", "pl"), currency=cfg.get("currency", "PLN"),
            max_stops=cfg.get("max_stops"),
        )
        return get_flights(query)

    def search(self, q: Query, cfg: dict) -> QueryResult:
        from fast_flights import FlightQuery

        offers, errors = [], []
        for o in q.origins:
            for d in q.destinations:
                if self.blocked_streak >= 2:
                    errors.append(f"{o}-{d}: pominięte – Google blokuje zapytania")
                    continue
                legs = [FlightQuery(date=q.outbound_date.isoformat(), from_airport=o, to_airport=d)]
                if q.return_date:
                    legs.append(FlightQuery(date=q.return_date.isoformat(), from_airport=d,
                                            to_airport=q.return_to or o))
                trip = "multi-city" if q.return_date else "one-way"

                res, err = None, None
                for attempt in range(2):
                    try:
                        res = self._fetch(legs, trip, cfg)
                        status = getattr(getattr(res, "diagnostics", None), "status", "ok")
                        if status in ("blocked", "http_error"):
                            err, res = f"{o}-{d}: Google: {status}", None
                        else:
                            err = None
                    except Exception as e:  # biblioteka rzuca różne wyjątki
                        err, res = f"{o}-{d}: {type(e).__name__}: {e}", None
                    if res is not None:
                        break
                    if attempt == 0:
                        self.sleep(60)
                if res is None:
                    self.blocked_streak += 1
                    errors.append(err)
                else:
                    self.blocked_streak = 0
                    offers.extend(self._parse(res, q, d, cfg))
                self.sleep(random.uniform(*self.delay))

        err = "; ".join(errors) if errors else None
        return QueryResult(q, offers=offers, error=err)

    @staticmethod
    def _parse(res, q: Query, dest: str, cfg: dict) -> list[Offer]:
        out = []
        for f in res:
            segs = getattr(f, "flights", None) or []
            price = getattr(f, "price", None)
            if not segs or not price:
                continue
            out.append(Offer(
                dest_group=q.dest_group,
                origin=segs[0].from_airport.code,
                destination=segs[-1].to_airport.code or dest,
                outbound_date=q.outbound_date,
                return_date=q.return_date,
                price=float(price),
                currency=cfg.get("currency", "PLN"),
                airlines=", ".join(sorted(set(f.airlines or []))) or "?",
                flight_numbers=" > ".join(f"{s.airline_code}{s.flight_number}" for s in segs),
                stops=len(segs) - 1,
                total_duration_min=None,  # Google podaje tylko czasy odcinków, bez przesiadek
                departure_time=_fmt_sdt(segs[0].departure),
                arrival_time=_fmt_sdt(segs[-1].arrival),
                return_to=q.return_to,
            ))
        return out


def _fmt_sdt(sdt) -> str | None:
    try:
        (y, m, d), (hh, mm) = sdt.date, sdt.time
        return f"{y:04d}-{m:02d}-{d:02d} {hh:02d}:{mm:02d}"
    except (AttributeError, TypeError, ValueError):
        return None


# ------------------------------------------------------------------- mock ---

class MockProvider:
    """Losowe, ale powtarzalne dane – do testów i podglądu raportów bez API."""

    BASE = {"CMB": 2900, "SGN": 3100, "HAN": 3300, "DAD": 3500}
    ORIGIN_DELTA = {"KRK": 250, "KTW": 200, "WAW": 0, "WMI": 150, "WRO": 220,
                    "GDN": 260, "POZ": 240, "PRG": 80}

    def __init__(self, seed: int | None = None, today: date | None = None):
        self.rng = random.Random(seed)
        self.today = today or date.today()

    def search(self, q: Query, cfg: dict) -> QueryResult:
        offers = []
        days_out = (q.outbound_date - self.today).days
        for o in q.origins:
            for d in q.destinations:
                for k in range(2):
                    base = self.BASE.get(d, 3000) + self.ORIGIN_DELTA.get(o, 200)
                    trend = max(0, 60 - days_out) * 18           # drożeje bliżej wylotu
                    price = round(base + trend + self.rng.gauss(0, 180) + k * 300, -1)
                    stops = 1 + k
                    if q.return_to and q.return_to != o:
                        price += 120          # bilet "tam z X, powrót do KRK" bywa droższy
                    offers.append(Offer(q.dest_group, o, d, q.outbound_date, q.return_date,
                                        price, cfg.get("currency", "PLN"), "Mock Air",
                                        f"MK-{o}{d}{k}", stops,
                                        900 + 240 * stops, return_to=q.return_to))
        lo = min(x.price for x in offers)
        ins = Insights(q.dest_group, q.outbound_date, q.return_date, lo, "typical", lo * 0.9, lo * 1.3)
        return QueryResult(q, offers=offers, insights=ins)


def get_provider(name: str, cfg: dict | None = None):
    cfg = cfg or {}
    if name == "serpapi":
        return SerpApiProvider()
    if name == "fast_flights":
        delay = (cfg.get("fast_flights") or {}).get("delay_seconds", [3, 6])
        return FastFlightsProvider(delay=(float(delay[0]), float(delay[1])))
    if name == "mock":
        return MockProvider()
    raise ValueError(f"Nieznany provider: {name}")
