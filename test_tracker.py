import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from collect import run
from report import build
from tracker.config import build_queries, departure_dates, load_config
from tracker.models import Offer, Query
from tracker.providers import MockProvider, SerpApiProvider, parse_serpapi
from tracker.ranking import dedupe, filter_stops, pick_top

ROOT = Path(__file__).resolve().parents[1]


def offer(origin, price, stops=1, fn="X1"):
    return Offer("wietnam", origin, "SGN", date(2027, 2, 5), date(2027, 2, 19), price, "PLN",
                 "Air", fn + origin, stops, 1000)


def test_queries_from_config():
    cfg = load_config(ROOT / "config.yaml")
    dates = departure_dates(cfg)
    assert dates[0] == date(2027, 2, 20) and dates[-1] == date(2027, 3, 5)
    assert len(dates) == 8                                    # co 2 dni + 5.03
    qs = build_queries(cfg, today=date(2026, 11, 14))
    assert len(qs) == 8 * 2 * 1
    q = qs[0]
    assert "KRK" in q.origins and "PRG" in q.origins
    assert q.return_date - q.outbound_date == timedelta(days=20)
    assert q.return_to == "KRK"


def test_date_list_still_supported():
    cfg = load_config(ROOT / "config.yaml")
    cfg["departure_dates"] = ["2027-02-21", "2027-02-25"]
    assert departure_dates(cfg) == [date(2027, 2, 21), date(2027, 2, 25)]


def test_past_dates_skipped():
    cfg = load_config(ROOT / "config.yaml")
    qs = build_queries(cfg, today=date(2027, 3, 1))
    assert qs and all(q.outbound_date > date(2027, 3, 1) for q in qs)


def test_krakow_preference_within_margin():
    offers = [offer("WAW", 3000), offer("KRK", 3100), offer("PRG", 3050), offer("KTW", 3500)]
    top = pick_top(offers, 3, ["KRK"], margin=150)
    assert [o.origin for o in top] == ["KRK", "WAW", "PRG"]   # KRK droższy o 100 < 150 → wygrywa
    assert [o.rank for o in top] == [1, 2, 3]


def test_other_airport_wins_when_much_cheaper():
    top = pick_top([offer("WAW", 2500), offer("KRK", 3100)], 1, ["KRK"], margin=150)
    assert top[0].origin == "WAW"


def test_dedupe_and_stops():
    offers = [offer("KRK", 3000), offer("KRK", 2900), offer("KRK", 2000, stops=3, fn="Z")]
    d = dedupe(offers)
    assert len(d) == 2 and min(o.price for o in d if o.flight_numbers == "X1KRK") == 2900
    assert all(o.stops <= 2 for o in filter_stops(d, 2))


SAMPLE = {
    "best_flights": [{
        "flights": [
            {"departure_airport": {"id": "KRK", "time": "2027-02-05 06:00"},
             "arrival_airport": {"id": "DOH", "time": "2027-02-05 12:30"},
             "airline": "Qatar Airways", "flight_number": "QR 286"},
            {"departure_airport": {"id": "DOH", "time": "2027-02-05 14:00"},
             "arrival_airport": {"id": "SGN", "time": "2027-02-06 01:10"},
             "airline": "Qatar Airways", "flight_number": "QR 970"},
        ],
        "total_duration": 970, "price": 3450,
    }],
    "other_flights": [{
        "flights": [
            {"departure_airport": {"id": "PRG", "time": "2027-02-05 10:00"},
             "arrival_airport": {"id": "HAN", "time": "2027-02-06 08:00"},
             "airline": "Emirates", "flight_number": "EK 140"}],
        "total_duration": 900, "price": 3200,
    }, {"flights": [], "price": 1}],
    "price_insights": {"lowest_price": 3200, "price_level": "low", "typical_price_range": [3300, 4500]},
}


def test_parse_serpapi():
    q = Query("wietnam", ("KRK", "PRG"), ("SGN", "HAN"), date(2027, 2, 5), date(2027, 2, 19))
    res = parse_serpapi(SAMPLE, q, "PLN")
    assert len(res.offers) == 2
    a, b = res.offers
    assert (a.origin, a.destination, a.stops, a.price) == ("KRK", "SGN", 1, 3450)
    assert a.flight_numbers == "QR 286 > QR 970"
    assert (b.origin, b.destination, b.stops) == ("PRG", "HAN", 0)
    assert res.insights.price_level == "low" and res.insights.typical_high == 4500


def test_serpapi_params(monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "test")
    cfg = load_config(ROOT / "config.yaml")
    q = build_queries(cfg, today=date(2026, 11, 14))[0]
    p = SerpApiProvider().params(q, cfg)
    assert p["type"] == "3" and p["currency"] == "PLN"            # multi-city
    legs = json.loads(p["multi_city_json"])
    assert legs[0]["departure_id"].startswith("KRK,") and legs[0]["arrival_id"] == "CMB"
    assert legs[1] == {"departure_id": "CMB", "arrival_id": "KRK", "date": "2027-03-12"}
    assert "departure_id" not in p


def test_serpapi_params_plain_round_trip(monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "test")
    q = Query("wietnam", ("KRK",), ("SGN",), date(2027, 2, 20), date(2027, 3, 12))
    p = SerpApiProvider().params(q, {"currency": "PLN"})
    assert p["type"] == "1" and p["return_date"] == "2027-03-12"


def test_end_to_end_mock(tmp_path):
    cfg = load_config(ROOT / "config.yaml")
    db = tmp_path / "p.sqlite"
    start = datetime(2026, 11, 14, 5, 0, tzinfo=timezone.utc)
    for i in range(12):  # 3 dni x 4 sprawdzenia
        now = start + timedelta(hours=6 * i)
        assert run(cfg, "mock", str(db), provider=MockProvider(seed=i, today=now.date()), now=now) == 0
    report = build(str(db), str(tmp_path / "reports")).read_text(encoding="utf-8")
    assert "TOP z ostatniego sprawdzenia" in report and "| 3 |" in report
    assert "powrót do KRK" in report
    assert (tmp_path / "reports" / "trend_wietnam.png").exists()


def test_missing_key(monkeypatch):
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    with pytest.raises(RuntimeError):
        SerpApiProvider()


# ---------------------------------------------------------- fast_flights ---

import sys
import types
from dataclasses import dataclass, field


def _fake_fast_flights(monkeypatch, responder):
    """Podstawia lekką atrapę biblioteki fast_flights (API faster-flights 3.8)."""
    mod = types.ModuleType("fast_flights")

    @dataclass
    class FlightQuery:
        date: str
        from_airport: str
        to_airport: str

    @dataclass
    class Passengers:
        adults: int = 1

    calls = []

    def create_query(**kw):
        calls.append(kw)
        return kw

    mod.FlightQuery, mod.Passengers, mod.create_query = FlightQuery, Passengers, create_query
    mod.get_flights = lambda q: responder(q)
    monkeypatch.setitem(sys.modules, "fast_flights", mod)
    return calls


@dataclass
class _Ap:
    code: str
    name: str = ""


@dataclass
class _Sdt:
    date: tuple
    time: tuple


@dataclass
class _Seg:
    from_airport: _Ap
    to_airport: _Ap
    departure: _Sdt
    arrival: _Sdt
    airline_code: str
    flight_number: str


@dataclass
class _Fl:
    price: int
    airlines: list
    flights: list


class _Res(list):
    def __init__(self, items, status="ok"):
        super().__init__(items)
        self.diagnostics = types.SimpleNamespace(status=status)


def _result_for(q):
    o, d = q["flights"][0].from_airport, q["flights"][0].to_airport
    seg1 = _Seg(_Ap(o), _Ap("DOH"), _Sdt((2027, 2, 20), (6, 5)), _Sdt((2027, 2, 20), (13, 0)), "QR", "286")
    seg2 = _Seg(_Ap("DOH"), _Ap(d), _Sdt((2027, 2, 20), (15, 0)), _Sdt((2027, 2, 21), (2, 30)), "QR", "970")
    base = {"KRK": 3300, "KTW": 3250, "WAW": 3000, "PRG": 3100}[o]
    return _Res([_Fl(base, ["Qatar Airways"], [seg1, seg2]), _Fl(0, [], [seg1])])


def test_fast_flights_multicity_back_to_krakow(monkeypatch):
    calls = _fake_fast_flights(monkeypatch, _result_for)
    from tracker.providers import FastFlightsProvider
    p = FastFlightsProvider(sleep=lambda s: None)
    cfg = load_config(ROOT / "config.yaml")
    q = build_queries(cfg, today=date(2026, 11, 14))[0]           # Sri Lanka, 20.02 → 12.03
    res = p.search(q, cfg)

    assert res.error is None and len(calls) == 4                  # 4 lotniska x CMB
    for c in calls:
        assert c["trip"] == "multi-city" and c["currency"] == "PLN"
        back = c["flights"][1]
        assert (back.from_airport, back.to_airport, back.date) == ("CMB", "KRK", "2027-03-12")
    assert len(res.offers) == 4                                     # oferta z ceną 0 odrzucona
    krk = next(o for o in res.offers if o.origin == "KRK")
    assert krk.destination == "CMB" and krk.stops == 1 and krk.return_to == "KRK"
    assert krk.flight_numbers == "QR286 > QR970" and krk.departure_time == "2027-02-20 06:05"

    top = pick_top(res.offers, 3, ["KRK"], 150)
    assert [o.origin for o in top] == ["WAW", "PRG", "KRK"]        # WAW tańszy o 300 > 150


def test_fast_flights_stops_after_repeated_blocking(monkeypatch):
    calls = _fake_fast_flights(monkeypatch, lambda q: _Res([], status="blocked"))
    from tracker.providers import FastFlightsProvider
    slept = []
    p = FastFlightsProvider(sleep=slept.append)
    cfg = load_config(ROOT / "config.yaml")
    res = p.search(build_queries(cfg, today=date(2026, 11, 14))[0], cfg)
    assert res.offers == [] and "blokuje" in res.error
    assert len(calls) == 4                    # 2 pary x 2 próby, potem przerwanie
    assert slept.count(60) == 2


def test_fast_flights_exception_is_reported(monkeypatch):
    def boom(q):
        raise ConnectionError("timeout")
    _fake_fast_flights(monkeypatch, boom)
    from tracker.providers import FastFlightsProvider
    cfg = load_config(ROOT / "config.yaml")
    res = FastFlightsProvider(sleep=lambda s: None).search(build_queries(cfg, today=date(2026, 11, 14))[0], cfg)
    assert "ConnectionError" in res.error
