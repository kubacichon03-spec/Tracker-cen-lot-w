"""Jedno sprawdzenie cen: pobiera oferty dla wszystkich zapytań z config.yaml,
wybiera top N i zapisuje wszystko do bazy SQLite.

Użycie:
    python collect.py                      # provider z config.yaml
    python collect.py --provider mock      # dane testowe, bez API
    python collect.py --dry-run            # pokaż, jakie zapytania by poszły
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from tracker.config import build_queries, load_config
from tracker.db import connect, save_run
from tracker.providers import get_provider
from tracker.ranking import dedupe, filter_stops, pick_top


def run(cfg: dict, provider_name: str, db_path: str, provider=None, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    queries = build_queries(cfg, today=now.date())
    if not queries:
        print("Brak zapytań – wszystkie daty w config.yaml są w przeszłości.")
        return 0

    provider = provider or get_provider(provider_name, cfg)
    offers, insights, errors = [], [], []
    for q in queries:
        res = provider.search(q, cfg)
        tag = f"{q.dest_group} {q.outbound_date}" + (f"→{q.return_date}" if q.return_date else "")
        if res.error:
            errors.append(f"{tag}: {res.error}")
            print(f"  ! {tag}: {res.error}")
        print(f"  · {tag}: {len(res.offers)} ofert")
        offers.extend(res.offers)
        if res.insights:
            insights.append(res.insights)

    offers = filter_stops(dedupe(offers), cfg.get("max_stops"))
    pref = cfg.get("preference") or {}
    top = pick_top(offers, cfg.get("top_n", 3), pref.get("preferred_origins", []), pref.get("margin", 0))

    con = connect(db_path)
    run_id = save_run(con, provider_name, offers, insights, len(queries), errors, now)
    con.close()

    print(f"\nRun #{run_id}: {len(offers)} ofert, {len(errors)} błędów. TOP {len(top)}:")
    for o in top:
        ret = f" ↔ {o.return_date}" + (f" (→{o.return_to})" if o.return_to else "") if o.return_date else ""
        print(f"  {o.rank}. {o.price:,.0f} {o.currency}  {o.origin}→{o.destination}  "
              f"{o.outbound_date}{ret}  {o.airlines}  przesiadki: {o.stops}")
    # błąd tylko gdy nie udało się nic pobrać
    return 1 if errors and not offers else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--db", default="data/prices.sqlite")
    ap.add_argument("--provider")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_config(args.config)
    provider_name = args.provider or cfg.get("provider", "serpapi")

    if args.dry_run:
        qs = build_queries(cfg)
        for q in qs:
            print(f"{q.dest_group:10} {','.join(q.origins)} → {','.join(q.destinations)}  "
                  f"{q.outbound_date} {q.return_date or ''}")
        n = len(qs)
        if provider_name == "fast_flights":
            n = sum(len(q.origins) * len(q.destinations) for q in qs)
        print(f"\n{n} zapytań na jedno sprawdzenie (provider: {provider_name})")
        return 0
    return run(cfg, provider_name, args.db)


if __name__ == "__main__":
    sys.exit(main())
