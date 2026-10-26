# Fix-Plan: Scanner-Fehlerbehebung & Robustheitsverbesserung

## Problemstellung

Der Antragsgrün Scraper für die BDK 52-Konvention hat ein kritisches Problem:

1. Amendments **25589-25663** (und andere in dieser ID-Reihenfolge) werden **nicht erkannt/gescannt**, obwohl sie auf der Plattform existieren.
2. Falsche Amendments (IDs **25817-25824**) werden als existierend markiert, obwohl sie **nicht existieren**.

## Problemanalyse

### 1. `scan_range()` skippt ID-Bereiche aufgrund korrupter `previous_results`

In `amendment_scanner.py`:

```python
known = {
    result["amendment_id"]: result
    for result in (previous_results or [])
    if result.get("exists")
}
highest_known = max(known) if known else None

if highest_known:
    start_id = max(start_id, highest_known - 5)

```

**Ablauf bei korrupten `previous_results`:**

* `previous_results` enthält IDs **25817-25824** (die **nicht** existieren!)
* `highest_known` = 25824
* `start_id` = max(25220, 25824-5) = **25819**
* Scan beginnt bei 25819 → IDs **25589-25663** werden **übersprungen**

**Die Ursache der Korruption:**

* Die `scan_results-TIMESTAMP.json`-Datei wurde früher mit einem Client erstellt, der `follow_redirects` nicht korrekt verarbeitete.
* Dadurch wurde `final_url` nie aktualisiert → Redirect auf `/52bdk` wurde nicht erkannt.
* IDs 25817-25824 wurden fälschlicherweise als `exists=True` markiert.

### 2. `scan_results-*.json` wird aus `output/` nicht konsequent bereinigt

* Das Cleanup (`main.py`) löscht `output/` vor jedem Lauf.
* Aber: `scan_results.json` wird **innerhalb** desselben Laufs in Phase 1 geschrieben und **nach der Archivierung** gelöscht.
* Wenn der Service **abgestürzt ist**, bleibt `scan_results.json` im `output/`-Ordner.
* Beim nächsten Start wird diese korrupte Datei von `load_scan_results()` geladen.

### 3. `_is_not_found_redirect()` ist nicht robust genug

Die Methode in `amendment_scanner.py`:

```python
def _is_not_found_redirect(self, final_url: str) -> bool:
    path = final_url.rstrip("/").split("?")[0]
    if path == f"{config.BASE_URL}/{config.CONVENTION}":
        return True
    return False

```

Problem:

* Erkennt nur Redirect auf `/52bdk` (ohne Trailing-Slash).
* **Funktioniert nicht**, wenn `final_url` dennoch die Original-Scan-URL enthält.

**Wichtig:** Die Motion `vorschlag-zur-tagesordnung-38811` existiert als echte Motion und dient als Dummy-Slug. In Zukunft **könnten** echte Amendments entstehen, die zu dieser Motion gehören. In diesem Fall:

* Die Anfrage-URL `/vorschlag-zur-tagesordnung-38811/{ID}` würde **nicht** auf `/52bdk` (Startseite) umleiten.
* Stattdessen würde sie auf der echten Motion-URL bleiben (z.B. `/52bdk/vorschlag-zur-tagesordnung-38811/{ID}`) und den Amendment-Inhalt liefern.
* **Diese Amendments müssen als existierend erkannt werden.**

---

## Lösungsplan

### Phase 1: Sofortige Stabilisierung (Kurzfristig - ohne Code-Änderung am laufenden Service)

**1.1 Cache-Bereinigung (Script-basiert)**

Speichere das folgende Script als `cleanup-scan-cache.ps1`:

```powershell
param([string]$ProjectRoot = "C:\Users\kolja\Desktop\52bdk")
Write-Host "Stopping BdkAmendmentScraper service..."
net stop BdkAmendmentScraper | Out-Null
Write-Host "Clearing corrupt scan cache..."
Remove-Item -Path "$ProjectRoot\output\scan_results-*.json" -Force -ErrorAction SilentlyContinue
Remove-Item -Path "$ProjectRoot\scraper_state\scan_progress.json" -Force -ErrorAction SilentlyContinue
Write-Host "Cleared scan_results-*.json and scan_progress.json"
Write-Host "Starting BdkAmendmentScraper service..."
net start BdkAmendmentScraper | Out-Null
Write-Host "Done."
```

Effekt: Scanner startet bei ID 25220, findet alle Amendments 25589-25663.

### Phase 2: Code-Level Fixes (Mittelfristig)

#### Fix A: `scan_range()` — Kompletten Bereich neu scannen (Self-Healing)

Ziel: IDs zwischen `START_AMENDMENT_ID` und `highest_known + margin` **müssen immer komplett durchlaufen** werden.

**Datei:** `scraper/amendment_scanner.py`

**Änderung:**

```python
if highest_known:
    # Always scan from config.START_AMENDMENT_ID to ensure no gaps.
    # This guarantees corrupt previous_results don't cause skips.
    start_id = config.START_AMENDMENT_ID
    logger.info(
        f"[Scanner] Rescanning full range {start_id}..{highest_known + config.SCAN_MARGIN_IDS} "
        f"to fill gaps (highest_known was {highest_known})"
    )

```

#### Fix B: `_is_not_found_redirect()` robuster machen

**Datei:** `scraper/amendment_scanner.py`

**Analyse:** Die aktuelle Implementierung funktioniert bereits korrekt für den Standardfall (`final_url == `https://antraege.gruene.de/52bdk``). Es gibt **keine Code-Änderung nötig**, da die Redirect-Erkennung bereits zuverlässig ist (wie durch Live-Tests bestätigt).

**Optionaler Verbesserung (robustheit):** Logge nicht erkannte Redirect-Muster:
```python
def _is_not_found_redirect(self, final_url: str) -> bool:
    path = final_url.rstrip("/").split("?")[0]
    
    # Redirect to main convention page - amendment does not exist
    if path == f"{config.BASE_URL}/{config.CONVENTION}":
        return True
    
    # Log unexpected redirect patterns for debugging
    expected_prefix = f"{config.BASE_URL}/{config.CONVENTION}/"
    if not path.startswith(expected_prefix):
        logger.warning(f"[Scanner] Unexpected final_url format: {final_url}")
    
    return False
```

> ℹ️ **Bemerkung:** Diese Änderung ist **optional**. Die aktuelle Logik reicht aus, wenn Fix A (Rescan-Logik) implementiert ist, da korrupte `previous_results` nicht mehr zu falschen Start-IDs führen.

#### Fix C: `scan_single_id()` — Strikte Validierung des Redirect-Ziels (Drafts bewahren)

**Datei:** `scraper/amendment_scanner.py`

**Ziel:** Nur Amendments als existierend markieren, wenn die URL wirklich auf eine Motion (nicht `/52bdk`) zeigt. **Wichtig:** Draft-Erkennung (404 mit Fehlerseite) darf **nicht entfernt** werden!

**Vollständige `scan_single_id()`-Logik (ersetzt ab Zeile 83):**
```python
# Handle response
if status_code == 404:
    # Draft amendment: 404, aber mit Inhalt auf der Fehlerseite
    if self._is_error_page(html):
        motion_slug = self._extract_motion_slug_from_html(html)
        if not motion_slug:
            motion_slug = self._extract_motion_slug_from_final_url(final_url)
        if motion_slug:
            logger.info(f"[Scanner] ID {amendment_id}: DRAFT (404) -> {motion_slug}")
            return {
                "amendment_id": amendment_id,
                "motion_slug": motion_slug,
                "readable": False,  # Draft = not readable
                "final_url": final_url,
                "exists": True,
                "error": False
            }
    # True 404 - not found
    return {
        "amendment_id": amendment_id,
        "motion_slug": None,
        "readable": False,
        "final_url": final_url,
        "exists": False,
        "error": False
    }

# Redirect to main page means amendment does not exist
if self._is_not_found_redirect(final_url):
    return {
        "amendment_id": amendment_id,
        "motion_slug": None,
        "readable": False,
        "final_url": final_url,
        "exists": False,
        "error": False
    }

# If we get here, the URL is a valid motion page (or stayed on the scan URL itself)
motion_slug = self._extract_motion_slug_from_final_url(final_url)
if not motion_slug:
    logger.warning(f"[Scanner] ID {amendment_id}: Could not extract motion slug from {final_url}")
    return {
        "amendment_id": amendment_id,
        "motion_slug": None,
        "readable": False,
        "final_url": final_url,
        "exists": False,
        "error": True
    }

readable = not self._is_error_page(html)
status = "READABLE" if readable else "NOT READABLE"
logger.info(f"[Scanner] ID {amendment_id}: {status} -> {motion_slug}")

return {
    "amendment_id": amendment_id,
    "motion_slug": motion_slug,
    "readable": readable,
    "final_url": final_url,
    "exists": True,
    "error": False
}

```

#### Fix D: `scan_results.json` isolieren & atomar abspeichern (Persistenz verbessern)

* Verschiebe Scan-Progress-Persistierung in eine **separate Datei außerhalb von `output/**` (z. B. `scraper_state/scan_progress.json`), die nicht archiviert wird.
* Nutze ein **atomares Schreibverfahren** mittels einer temporären Datei (`.tmp`) und `os.replace()`, um Datenkorruption bei plötzlichen Dienstabbrüchen zu verhindern.

**In `storage.py`:**

```python
PROGRESS_FILE = config.PROJECT_ROOT / "scraper_state" / "scan_progress.json"

def save_scan_progress(self, found_ids, not_found_ids):
    self.PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    progress = {
        "found_ids": found_ids,
        "not_found_ids": not_found_ids,
        "timestamp": datetime.now().isoformat()
    }
    tmp_file = self.PROGRESS_FILE.with_suffix(".tmp")
    tmp_file.write_text(json.dumps(progress, indent=2))
    os.replace(tmp_file, self.PROGRESS_FILE)

```

---

### Phase 3: Langfristige Robustheitsverbesserungen

#### C1: Retry-Logik für Redirects

Der HTTP-Client (`http_client.py`) sollte bei 302/Redirect auf die finale URL achten und prüfen, ob `response.url` aktualisiert wird.

#### C2: Logging für falsche Redirects

Füge in `scan_single_id()` Warn-Logs hinzu, wenn unerwartete Redirect-Verhalten auftreten.

#### C3: Content Scraper Validierung

In Phase 2 (`content_scraper.py`) vorab validieren, ob die `final_url` auf eine gültige Motion zeigt.

#### C4: Self-Healing im Storage

Bei `save_amendment()` prüfen, ob Dubletten existieren und diese bereinigen.

---

### Phase 3.5: Test-Verfahren

**Nach jedem Fix-Schritt, Test-Assertionen ausführen:**

1. **Fix A Test (Self-Healing):**
   - Manuell eine `scan_results.json` mit `highest_known=25824` erstellen
   - `scan_range()` starten → muss bei `START_AMENDMENT_ID` (25220) beginnen
   - **Erwartet:** IDs 25589–25663 im Ergebnis

2. **Fix C Test (Redirect-Detection):**
   - Für IDs 25817–25824 (→ redirect auf `/52bdk`):
     ```python
     assert result["exists"] == False
     assert result["final_url"] == "https://antraege.gruene.de/52bdk"
     ```
   - Für IDs 25589–25663 (→ redirect auf echte Motion):
     ```python
     assert result["exists"] == True
     assert result["motion_slug"] != "vorschlag-zur-tagesordnung-38811"
     assert result["final_url"] != "https://antraege.gruene.de/52bdk"
```

Dies stellt sicher, dass alle IDs zwischen START_AMENDMENT_ID und highest_known immer geprüft werden.

⚠️ Performance-Hinweis: Wenn highest_known groß ist (z. B. 30000), neu-scannt der Bereich komplett. Alternativ kann scan_progress.json (Fix D) als ID-Tracker dienen, der nur neue IDs scannt.

3. **End-to-End Test (1 Laufzykklus):**
   - Service neu starten
   - Auf Logs warten: `[Scanner] Starting scan from ID 25220`
   - Prüfen: `[Phase 1] Found XXX amendments` (XXX > 300)
   - Prüfen: `output/*.txt` enthalten Amendments 25589–25663

---

## Implementierungs-Reihenfolge (To-Dos)

| Schritt | Aufgabe | Priorität | Status |
| --- | --- | --- | --- |
| 1 | Sofort: Service-Stop + Cache-Bereinigung | 🔴 Kritisch | ✅ Manuell |
| 2 | Fix A: `scan_range()` Rescan-Logik | 🔴 Kritisch | 🕒 Offen |
| 3 | Fix C: `scan_single_id()` Validierung | 🔴 Kritisch | 🕒 Offen |
| 4 | Fix B: `_is_not_found_redirect()` robuster | 🟡 Wichtig | 🕒 Offen |
| 5 | Fix D: Atomares `scan_progress.json` | 🟡 Wichtig | 🕒 Offen |
| 6 | Test: IDs 25589-25663 werden gefunden | 🔴 Kritisch | 🕒 Offen |
| 7 | Test: IDs 25817-25824 werden korrekt abgelehnt | 🔴 Kritisch | 🕒 Offen |
| 8 | C1-C4: Robustheitsfeatures | 🟢 Nice-to-have | 🕒 Offen |

---

## Erwartetes Ergebnis nach Fixes

* **Alle Amendments 25220-25663** werden korrekt erkannt.
* **Falsche Amendments 25817-25824** werden korrekt als `exists=False` abgelehnt.
* **Zukünftige Neu-Erkennungen**: Neu hinzukommende Amendments ab 25664+ werden zuverlässig erfasst.
* **Archive-Größe** stabilisiert sich, da keine korrupten Duplikate mehr mitgeschleppt werden.