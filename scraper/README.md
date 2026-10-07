# Antragsgrün Amendment Scraper

Scraper für die Antragsgrün Convention `52bdk` auf `https://antraege.gruene.de/52bdk`.

## Funktionen

- **Phase 1:** Scannt Amendment-IDs und findet alle existierenden Amendments
- **Phase 2:** Scraped den Inhalt von Amendments
- **Phase 3:** Scraped Motion-Seiten von der Startseite

## Installation

```bash
pip install -r requirements.txt
```

## Verwendung

```bash
# Alle Phasen ausführen
python main.py

# Nur Phase 1 (ID-Scan)
python main.py --phase 1

# Nur Phase 2 (Content-Scraping)
python main.py --phase 2

# Nur Phase 3 (Motion-Scraping)
python main.py --phase 3

# Mit mehr Worker-Threads (Standard: 10)
python main.py --workers 15

# Ohne Parallelisierung (Sequentiell)
python main.py --no-threading

# Ohne Tor (direkte HTTP-Requests)
python main.py --no-tor
```

## Ausgabe

- `output/` - Verzeichnis mit allen gescrapten Dateien
- `output/amendment_urls.txt` - Liste aller gefundenen Amendment-URLs
- `output/scan_results-*.json` - Scan-Ergebnisse für Resume-Funktionalität

## Konfiguration

Einstellungen können in `config.py` angepasst werden:

- `START_AMENDMENT_ID` - Start-ID für den Scan
- `MAX_CONSECUTIVE_NOT_FOUND` - Anzahl aufeinanderfolgender 404s bis zum Abbruch
- `REQUEST_DELAY_SECONDS` - Verzögerung zwischen Requests
- `MAX_WORKERS` - Anzahl der Worker-Threads für Parallelisierung
- `KNOWN_MOTION_SLUG` - Bekannter Motion-Slug für Phase 1
- `TOR_SOCKS_HOST` / `TOR_SOCKS_PORT` - Tor SOCKS5 Proxy Einstellungen

## Performance

- **Parallelisiert:** Bis zu 10 Worker-Threads
- **Tor Integration:** Über SOCKS5 Proxy (kein Selenium/Browser-Overhead)
- **Geschwindigkeit:** ~0.3s pro Amendment (vs. ~5-10s vorher)
- **Speedup:** ~20-50x schneller als vorher

## Projektstruktur

```
scraper/
├── main.py                 # Entry-Point
├── config.py               # Konfiguration
├── tor_client.py           # Tor SOCKS5 HTTP Client
├── amendment_scanner.py    # Phase 1: ID-Scan
├── content_scraper.py      # Phase 2: Content-Scraping
├── motion_scraper.py       # Phase 3: Motion-Scraping
├── storage.py              # Datei-Speicherung
├── requirements.txt
└── README.md
```
