from __future__ import annotations

from .models import Offer


def dedupe(offers: list[Offer]) -> list[Offer]:
    """Ten sam lot (te same numery, daty) może wrócić kilka razy – zostaw najtańszy."""
    best: dict[tuple, Offer] = {}
    for o in offers:
        key = (o.origin, o.destination, o.outbound_date, o.return_date, o.flight_numbers)
        if key not in best or o.price < best[key].price:
            best[key] = o
    return list(best.values())


def filter_stops(offers: list[Offer], max_stops: int | None) -> list[Offer]:
    if max_stops is None:
        return offers
    return [o for o in offers if o.stops <= max_stops]


def pick_top(offers: list[Offer], top_n: int, preferred: list[str], margin: float) -> list[Offer]:
    """Wybiera top_n ofert. Preferowane lotniska (np. KRK) dostają 'bonus' = margin,
    czyli lot z innego lotniska wygrywa tylko, gdy jest tańszy o więcej niż margin.
    Oznacza wybrane oferty polem rank (1 = najlepsza)."""
    preferred_set = set(preferred or [])
    for o in offers:
        o.effective_price = o.price - (margin if o.origin in preferred_set else 0)
        o.rank = None

    ordered = sorted(offers, key=lambda o: (o.effective_price, o.price, o.stops, o.total_duration_min or 10**6))
    top = ordered[:top_n]
    for i, o in enumerate(top, start=1):
        o.rank = i
    return top
