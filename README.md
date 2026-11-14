# Tracker cen lotów: KRK / KTW / WAW / PRG → Sri Lanka i Wietnam, powrót do Krakowa

Darmowy tracker. Kilka razy dziennie sprawdza ceny biletów w Google Flights, zapisuje
**wszystkie** znalezione oferty do bazy SQLite, wybiera **3 najkorzystniejsze połączenia**
i buduje raport z trendami cen.

- Wyloty: Kraków, Katowice, Warszawa (Chopin), Praga
- Cele: Sri Lanka (CMB) i Wietnam (SGN, HAN, DAD)
- Daty wylotu: 20.02 – 5.03.2027 (co 2 dni), pobyt 20 dni
- **Powrót zawsze do Krakowa**: bilet multi-city (tam z dowolnego z 4 lotnisk, z powrotem do KRK),
  cena to łączny koszt całego biletu
- **Kraków ma pierwszeństwo**: inne lotnisko trafia wyżej tylko wtedy, gdy jest tańsze
  o więcej niż `preference.margin` (domyślnie 150 PLN)
- Wszystko ustawiasz w `config.yaml`

## Źródło danych: faster-flights (darmowe)

Biblioteka `faster-flights` czyta Google Flights bezpośrednio: nie trzeba klucza ani płacić.
W zamian:
- pyta o każdą parę lotnisk osobno: 4 wyloty × 4 cele × 8 dat = **128 zapytań na sprawdzenie**
  (ok. 15–20 min z przerwami między zapytaniami),
- Google może zacząć blokować zapytania. Tracker wtedy odczekuje, próbuje ponownie, a po dwóch
  blokadach z rzędu przerywa sprawdzenie (błędy widać w raporcie),
- biblioteka może przestać działać po zmianach po stronie Google. Wtedy: `pip install -U faster-flights`.

Uwaga: zainstaluj `faster-flights`, a **nie** `fast-flights`. Oryginalna paczka nie obsługuje
biletów multi-city. Obie mają ten sam import (`fast_flights`), więc nie instaluj obu naraz.

Płatna alternatywa (stabilniejsza, jedno zapytanie na kraj i datę): `provider: serpapi`
+ sekret `SERPAPI_KEY`.

## Jak to działa

```
GitHub Actions (cron 3×/dzień)
   └─ collect.py  → Google Flights (faster-flights) → data/prices.sqlite
   └─ report.py   → reports/REPORT.md + wykresy PNG
   └─ commit do repo (historia cen zostaje w repozytorium)
```

## Uruchomienie lokalne

```bash
pip install -r requirements.txt
python collect.py --dry-run           # pokaże, jakie zapytania pójdą
python collect.py --provider mock     # test na danych udawanych, bez internetu
python collect.py                     # prawdziwe sprawdzenie
python report.py                      # raport w reports/REPORT.md
python -m pytest -q                   # testy
```

## Uruchomienie automatyczne (GitHub Actions)

1. Wrzuć ten folder do nowego repozytorium na GitHubie.
2. Zakładka **Actions** → „Sprawdzanie cen lotów” → **Run workflow**, żeby sprawdzić od razu.
   Później uruchamia się samo o ok. 7:13, 15:13 i 23:13 czasu polskiego.
3. Raport: plik `reports/REPORT.md` w repo (GitHub wyświetla go z wykresami).

Jeśli w raporcie pojawiają się błędy „blocked”, Google blokuje serwery GitHuba. Wtedy najprościej
uruchamiać `collect.py` i `report.py` na własnym komputerze (np. Harmonogram zadań w Windows
albo cron), bo zapytania z domowego internetu są blokowane znacznie rzadziej.

## Co zawiera raport

- TOP 3 z ostatniego sprawdzenia (z uwzględnieniem preferencji Krakowa)
- najtańsze oferty osobno dla Sri Lanki i Wietnamu + najtańsza cena z Krakowa
- 3 najniższe ceny każdego dnia
- trend dla każdej daty wylotu: cena teraz, minimum, mediana, zmiana od pierwszego sprawdzenia
- czy godzina sprawdzenia wpływa na cenę i jak cena zmienia się wraz z liczbą dni do wylotu

## Baza danych

`data/prices.sqlite`, tabele:
- `runs`: każde sprawdzenie (czas UTC, liczba zapytań, błędy)
- `offers`: każda oferta (trasa, daty, lotnisko powrotu, cena, linie, przesiadki, `rank` 1–3 dla TOP)
- `insights`: ocena ceny od Google (tylko przy provider: serpapi)

Bazę można otworzyć np. w DB Browser for SQLite albo wczytać do Excela/pandas.
