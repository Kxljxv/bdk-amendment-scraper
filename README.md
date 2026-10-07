# BDK Amendment Scraper (52bdk)

Automated scraper for Antragsgruen amendments from the BDK 52 convention. Runs as a Windows service on a Mini-PC with monitoring, auto-restart, and NAS backup.

## Features

- **Phased scraping**: Amendment ID scanning → Content scraping → Motion scraping
- **Google Sheets export**: Custom column order, breadcrumb parsing, notes preservation
- **No Tor**: Direct HTTP with connection pooling, retry logic, exponential backoff
- **Windows Service**: NSSM with auto-restart on failure, delayed start
- **Monitoring**: Health endpoint for Uptime Kuma (Docker)
- **Backup**: Daily robocopy to NAS with retention
- **Hardening**: Windows 11 24/7 optimization script

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌──────────────────┐
│ Phase 1     │     │ Phase 2     │     │ Phase 3          │
│ Scan IDs    │────▶│ Scrape      │     │ Scrape Motions   │
│ (parallel)  │     │ Content     │     │ (parallel)       │
└─────────────┘     └─────────────┘     └──────────────────┘
                           │
                           ▼
                    ┌─────────────┐
                    │ Google      │
                    │ Sheets      │
                    │ Export      │
                    └─────────────┘
                           │
                           ▼
                    ┌─────────────┐     ┌─────────────┐
                    │ Archive ZIP │────▶│ NAS Backup  │
                    │ (per run)   │     │ (daily)     │
                    └─────────────┘     └─────────────┘
```

## Quick Start

### Prerequisites

- Windows 11 Pro/Enterprise
- Python 3.10+
- Google Service Account with Sheets API access
- NAS share for backups (optional)

### Installation

```powershell
# 1. Clone repository
git clone https://github.com/your-org/bdk-amendment-scraper.git
cd bdk-amendment-scraper

# 2. Install Python dependencies
pip install -r scraper/requirements.txt

# 3. Place service account key
# Copy your-service-account.json to scraper/dienstkonto-schluessel/bdk-scraper-2-bf757e265e90.json

# 4. Harden Windows (run once as Admin)
.\windows-hardening.ps1 -NasSharePath "\\NAS\Backups\bdk-scraper"

# 5. Install as Windows Service (run as Admin)
.\setup-stable-service.ps1

# 6. Start monitoring (optional)
docker compose -f docker-compose.uptime-kuma.yml up -d
# Open http://localhost:3001 and add monitor: http://localhost:8080/health
```

### Configuration

Edit `scraper/config.py`:

```python
BASE_URL = "https://antraege.gruene.de"
CONVENTION = "52bdk"
START_AMENDMENT_ID = 25220
SCRAPE_INTERVAL_MINUTES = 10
MAX_WORKERS = 10
```

## Service Management

```powershell
# Start/Stop/Restart
net start BdkAmendmentScraper
net stop BdkAmendmentScraper

# Check status
sc query BdkAmendmentScraper

# View logs
Get-Content C:\Users\kolja\Desktop\52bdk\output\service-stdout.log -Wait
Get-Content C:\Users\kolja\Desktop\52bdk\output\service-stderr.log -Wait

# Uninstall
.\uninstall-service.ps1
```

## Monitoring

### Uptime Kuma

1. Start: `docker compose -f docker-compose.uptime-kuma.yml up -d`
2. Open http://localhost:3001
3. Add Monitor:
   - Type: HTTP(s)
   - URL: `http://<mini-pc-ip>:8080/health`
   - Interval: 60 seconds
   - Accepted Status Codes: 200

### Health Endpoint

- `GET /health` - JSON health status
- `GET /metrics` - Prometheus metrics

Returns `200 OK` if last run < 30 min ago and archives exist, else `503`.

## Backup

Daily at 03:30 via Task Scheduler:

```powershell
# Create scheduled task
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-ExecutionPolicy Bypass -File C:\Users\kolja\Desktop\52bdk\backup-task.ps1 -NasPath '\\NAS\Backups\bdk-scraper'"
$Trigger = New-ScheduledTaskTrigger -Daily -At 03:30
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "BDK Scraper Backup" -Action $Action -Trigger $Trigger -Settings $Settings -RunLevel Highest -Force
```

## Google Sheets Export

Columns (A-K):
1. Amendment ID
2. URL
3. Motion Prefix (breadcrumb)
4. Eingereicht? (Ja/Nein)
5. Anzahl Antragsteller
6. Frauenanteil
7. Status
8. Antragsteller (main only)
9. Zeitpunkt Angelegt
10. Zuletzt aktualisiert
11. Notizen (preserved)

Sorted by Amendment ID descending.

## Project Structure

```
52bdk/
├── scraper/
│   ├── main.py                 # Entry point, periodic loop
│   ├── config.py               # Configuration
│   ├── http_client.py          # Robust HTTP client (httpx + tenacity)
│   ├── storage.py              # File storage with metadata
│   ├── amendment_scanner.py    # Phase 1: ID scanning
│   ├── content_scraper.py      # Phase 2: Content scraping
│   ├── motion_scraper.py       # Phase 3: Motion scraping
│   ├── google_sheets_exporter.py # Sheets export
│   ├── health_endpoint.py      # /health endpoint
│   └── requirements.txt
├── setup-stable-service.ps1    # NSSM service installer
├── uninstall-service.ps1       # Service removal
├── windows-hardening.ps1       # Windows 24/7 hardening
├── backup-task.ps1             # Daily NAS backup
├── docker-compose.uptime-kuma.yml # Monitoring stack
└── .gitignore
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Service won't start | Check `service-stderr.log`, verify Python path |
| Sheets export fails | Verify service account JSON, Sheet ID, worksheet name |
| No amendments found | Check `START_AMENDMENT_ID`, network connectivity |
| High memory usage | Reduce `MAX_WORKERS`, check for leaks |
| Backup fails | Verify NAS path, permissions, network |

## License

Internal use only - BDK 52 convention.