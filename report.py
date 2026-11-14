"""Raport z zebranych cen: reports/REPORT.md + wykresy PNG.

Użycie:  python report.py [--db data/prices.sqlite] [--out reports]
"""
from __future__ import annotations

import argparse
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

TZ = ZoneInfo("Europe/Warsaw")


def local(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ)


def fmt(p: float | None, cur: str = "PLN") -> str:
    return "—" if p is None else f"{p:,.0f} {cur}".replace(",", " ")


def trip(o: sqlite3.Row) -> str:
    back = f" (powrót do {o['return_to']})" if o["return_to"] else ""
    return o["outbound_date"] + (f" ↔ {o['return_date']}{back}" if o["return_date"] else "")


def offer_row(o: sqlite3.Row) -> str:
    return (f"| {fmt(o['price'], o['currency'])} | {o['origin']}→{o['destination']} | {trip(o)} | "
            f"{o['airlines']} | {o['stops']} |")


def build(db: str, out: str) -> Path:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    outp = Path(out)
    outp.mkdir(parents=True, exist_ok=True)
    md: list[str] = ["# Raport cen lotów: Polska/Praga → Sri Lanka / Wietnam (powrót do Krakowa)", ""]

    runs = con.execute("SELECT * FROM runs ORDER BY id").fetchall()
    if not runs:
        (outp / "REPORT.md").write_text("Brak danych – uruchom najpierw collect.py\n", encoding="utf-8")
        return outp / "REPORT.md"
    last = runs[-1]
    md += [f"Ostatnie sprawdzenie: **{local(last['checked_at']):%Y-%m-%d %H:%M}** (czas PL) · "
           f"liczba sprawdzeń: {len(runs)} · od {local(runs[0]['checked_at']):%Y-%m-%d}", ""]

    # --- TOP z ostatniego sprawdzenia
    top = con.execute("SELECT * FROM offers WHERE run_id=? AND rank IS NOT NULL ORDER BY rank",
                      (last["id"],)).fetchall()
    md += ["## TOP z ostatniego sprawdzenia", "",
           "| # | Cena | Trasa | Daty | Linie | Przesiadki |", "|---|---|---|---|---|---|"]
    md += [f"| {o['rank']} " + offer_row(o) for o in top]
    md.append("")

    # --- najtańsze per grupa (kraj) w ostatnim sprawdzeniu
    md += ["## Najtaniej teraz – osobno dla każdego kraju", ""]
    for g in [r[0] for r in con.execute("SELECT DISTINCT dest_group FROM offers ORDER BY 1")]:
        rows = con.execute("SELECT * FROM offers WHERE run_id=? AND dest_group=? ORDER BY price LIMIT 3",
                           (last["id"], g)).fetchall()
        if not rows:
            continue
        krk = con.execute("SELECT MIN(price) FROM offers WHERE run_id=? AND dest_group=? AND origin='KRK'",
                          (last["id"], g)).fetchone()[0]
        md += [f"**{g}** – najtaniej z Krakowa: {fmt(krk)}", "",
               "| Cena | Trasa | Daty | Linie | Przesiadki |", "|---|---|---|---|---|"]
        md += [offer_row(o) for o in rows]
        md.append("")

    # --- TOP 3 dnia (najlepsze z całego dnia, po wszystkich sprawdzeniach)
    md += ["## 3 najniższe ceny każdego dnia (ostatnie 14 dni, ze wszystkich sprawdzeń)", "",
           "| Dzień | 1. | 2. | 3. |", "|---|---|---|---|"]
    by_day: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for o in con.execute("SELECT * FROM offers WHERE rank IS NOT NULL"):
        by_day[f"{local(o['checked_at']):%Y-%m-%d}"].append(o)
    for day in sorted(by_day)[-14:][::-1]:
        seen, best = set(), []
        for o in sorted(by_day[day], key=lambda r: r["price"]):
            key = (o["origin"], o["destination"], o["outbound_date"], o["return_date"], o["flight_numbers"])
            if key not in seen:
                seen.add(key)
                best.append(o)
        cells = [f"{fmt(o['price'])} {o['origin']}→{o['destination']} {o['outbound_date']}" for o in best[:3]]
        md.append(f"| {day} | " + " | ".join(cells + [""] * (3 - len(cells))) + " |")
    md.append("")

    # --- trend: najniższa cena per (kraj, data wylotu) w każdym sprawdzeniu
    md += ["## Jak zmienia się cena (najtańsza oferta z dowolnego lotniska)", "",
           "| Kraj | Wylot | Teraz | Min. widziane | Mediana | Zmiana od 1. sprawdzenia | Z Krakowa teraz |",
           "|---|---|---|---|---|---|---|"]
    series = con.execute("""
        SELECT dest_group, outbound_date, return_date, run_id, checked_at, MIN(price) AS p,
               MIN(CASE WHEN origin='KRK' THEN price END) AS krk
        FROM offers GROUP BY dest_group, outbound_date, return_date, run_id ORDER BY run_id""").fetchall()
    grouped: dict[tuple, list] = defaultdict(list)
    for r in series:
        grouped[(r["dest_group"], r["outbound_date"], r["return_date"])].append(r)

    for (g, outd, retd), pts in sorted(grouped.items()):
        prices = [p["p"] for p in pts]
        cur, first = prices[-1], prices[0]
        delta = cur - first
        pct = f"{delta:+,.0f} ({delta / first:+.0%})".replace(",", " ") if first else "—"
        md.append(f"| {g} | {outd}{' ↔ ' + retd if retd else ''} | {fmt(cur)} | {fmt(min(prices))} | "
                  f"{fmt(statistics.median(prices))} | {pct} | {fmt(pts[-1]['krk'])} |")
    md.append("")

    # --- wykresy per kraj
    for g in sorted({k[0] for k in grouped}):
        fig, ax = plt.subplots(figsize=(10, 4.5))
        for (gg, outd, retd), pts in sorted(grouped.items()):
            if gg != g:
                continue
            xs = [local(p["checked_at"]) for p in pts]
            ax.plot(xs, [p["p"] for p in pts], marker="o", ms=3, label=f"wylot {outd}")
        ax.set_title(f"{g}: najniższa cena w kolejnych sprawdzeniach")
        ax.set_ylabel("PLN")
        ax.grid(alpha=.3)
        ax.legend(fontsize=8)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
        fig.autofmt_xdate()
        fig.tight_layout()
        name = f"trend_{g}.png"
        fig.savefig(outp / name, dpi=110)
        plt.close(fig)
        md += [f"![{g}]({name})", ""]

    # --- wpływ godziny sprawdzenia i dni do wylotu
    hour_dev: dict[int, list[float]] = defaultdict(list)
    dtd: dict[int, list[float]] = defaultdict(list)
    for (g, outd, retd), pts in grouped.items():
        med = statistics.median(p["p"] for p in pts)
        for p in pts:
            t = local(p["checked_at"])
            hour_dev[t.hour].append((p["p"] - med) / med)
            days = (datetime.fromisoformat(outd).date() - t.date()).days
            dtd[days // 7 * 7].append(p["p"])
    if len(hour_dev) > 1:
        md += ["## Czy godzina sprawdzenia ma znaczenie?", "",
               "Średnie odchylenie od mediany danej trasy (minus = taniej):", "",
               "| Godzina (PL) | Odchylenie | Liczba pomiarów |", "|---|---|---|"]
        md += [f"| {h:02d}:00 | {statistics.mean(v):+.1%} | {len(v)} |" for h, v in sorted(hour_dev.items())]
        md.append("")
    if len(dtd) > 1:
        md += ["## Cena a liczba dni do wylotu", "", "| Dni do wylotu | Średnia cena | Pomiary |", "|---|---|---|"]
        md += [f"| {d}–{d + 6} | {fmt(statistics.mean(v))} | {len(v)} |" for d, v in sorted(dtd.items(), reverse=True)]
        md.append("")

    # --- price insights z Google
    ins = con.execute("SELECT * FROM insights WHERE run_id=? ORDER BY dest_group, outbound_date",
                      (last["id"],)).fetchall()
    if ins:
        md += ["## Ocena Google Flights (ostatnie sprawdzenie)", "",
               "| Kraj | Wylot | Najniższa | Poziom | Typowy zakres |", "|---|---|---|---|---|"]
        md += [f"| {i['dest_group']} | {i['outbound_date']} | {fmt(i['lowest_price'])} | {i['price_level'] or '—'} | "
               f"{fmt(i['typical_low'])} – {fmt(i['typical_high'])} |" for i in ins]
        md.append("")

    if last["n_errors"]:
        md += ["## Błędy w ostatnim sprawdzeniu", "", "```", last["errors"] or "", "```", ""]

    path = outp / "REPORT.md"
    path.write_text("\n".join(md), encoding="utf-8")
    con.close()
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/prices.sqlite")
    ap.add_argument("--out", default="reports")
    a = ap.parse_args()
    print(f"Zapisano {build(a.db, a.out)}")
