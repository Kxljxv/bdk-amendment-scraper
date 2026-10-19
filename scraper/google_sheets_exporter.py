"""
Google Sheets Exporter.

Exports scraped amendment data to a Google Spreadsheet.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List

from bs4 import BeautifulSoup

try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False

from config import config


class GoogleSheetsExporter:
    """Exports amendment data to Google Sheets."""

    def __init__(self):
        if not GSPREAD_AVAILABLE:
            raise ImportError(
                "gspread and google-auth are required. "
                "Install with: pip install gspread google-auth"
            )
        self.service_account_file = (
            config.PROJECT_ROOT / config.GOOGLE_SERVICE_ACCOUNT_FILE
        )
        self.spreadsheet_id = config.GOOGLE_SPREADSHEET_ID
        self.worksheet_name = config.GOOGLE_WORKSHEET_NAME
        self.muster_worksheet_name = "Muster"
        self.scopes = [
            "https://www.googleapis.com/auth/spreadsheets"
        ]

    def _authenticate(self):
        """Authenticate with Google Sheets API using service account."""
        try:
            credentials = Credentials.from_service_account_file(
                self.service_account_file,
                scopes=self.scopes
            )
            client = gspread.authorize(credentials)
            return client
        except Exception as e:
            print(f"[SheetsExporter] Authentication failed: {e}")
            return None

    def _fetch_motion_prefix(self, motion_slug: str) -> str:
        """
        Fetch motion prefix from motion page breadcrumb.
        
        Args:
            motion_slug: The motion slug
            
        Returns:
            Motion prefix string (e.g., "S-01", "V-08") or empty string
        """
        try:
            import requests
            motion_url = f"{config.BASE_URL}/{config.CONVENTION}/{motion_slug}"
            resp = requests.get(motion_url, timeout=15)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                breadcrumb = soup.find(class_="breadcrumb")
                if breadcrumb:
                    items = breadcrumb.find_all("li")
                    if len(items) >= 2:
                        return items[1].get_text(strip=True)
        except Exception:
            pass
        return ""

    def _parse_breadcrumb(self, breadcrumb_html: str) -> str:
        """
        Extract motion prefix from breadcrumb HTML.
        
        Args:
            breadcrumb_html: The HTML content of the breadcrumb element
            
        Returns:
            Motion prefix string (e.g., "S-01", "V-08")
        """
        if not breadcrumb_html:
            return ""
        
        try:
            soup = BeautifulSoup(breadcrumb_html, "html.parser")
            items = soup.find_all("li")
            if len(items) >= 2:
                return items[1].get_text(strip=True)
        except Exception:
            pass
        
        return ""

    def _parse_amendment_file(self, file_path: Path) -> Optional[dict]:
        """
        Parse an amendment .txt file and extract metadata.

        Args:
            file_path: Path to the amendment .txt file

        Returns:
            Dictionary with amendment data or None if parsing fails
        """
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            print(f"[SheetsExporter] Error reading {file_path.name}: {e}")
            return None

        data = {
            "amendmentId": "",
            "url": "",
            "motionSlug": "",
            "readable": "",
            "breadcrumb_html": "",
            "motionPrefix": "",
            "eingereicht": "",
            "status": "",
            "antragssteller": "",
            "zeitpunktAngelegt": "",
            "zuletztAktualisiert": "",
            "anzahlAntragssteller": "",
            "frauenanteil": "",
            "notizen": "",
        }

        # Parse metadata header
        metadata_match = re.search(
            r"=== METADATEN ===\n(.*?)===",
            content,
            re.DOTALL
        )
        if metadata_match:
            metadata_text = metadata_match.group(1)
            for line in metadata_text.strip().split("\n"):
                if ":" in line:
                    key, value = line.split(":", 1)
                    key = key.strip()
                    value = value.strip()
                    if key in data:
                        data[key] = value

        # Parse breadcrumb
        breadcrumb_match = re.search(
            r"=== BREADCRUMB ===\n(.*?)===",
            content,
            re.DOTALL
        )
        if breadcrumb_match:
            data["breadcrumb_html"] = breadcrumb_match.group(1).strip()
            data["motionPrefix"] = self._parse_breadcrumb(data["breadcrumb_html"])

        # Extract additional data from HTML content
        html_match = re.search(r"=== INHALT ===\n(.*)", content, re.DOTALL)
        if html_match:
            html_content = html_match.group(1)
            try:
                soup = BeautifulSoup(html_content, "html.parser")
                
                # Check if this is an error page (non-readable amendment)
                error_indicator = soup.find(string=lambda text: text and "kann nicht angezeigt werden" in text)
                if error_indicator:
                    data["status"] = "nichtveröffentlicher Entwurf"
                    data["eingereicht"] = "Nein"
                    data["readable"] = "False"
                else:
                    # Extract data from motionDataTable
                    table = soup.find(class_="motionDataTable")
                    if table:
                        for row in table.find_all("tr"):
                            th = row.find("th")
                            td = row.find("td")
                            if th and td:
                                key = th.get_text(strip=True)
                                value = td.get_text(strip=True)
                                
                                if "Status" in key:
                                    # Strip suffix like "(Berechtigung: Ausgewählte Gruppen)"
                                    status = value
                                    if "(" in status:
                                        status = status.split("(")[0].strip()
                                    data["status"] = status
                                    # Set "Eingereicht?" based on status
                                    if status in ["Eingereicht", "Eingereicht "]:
                                        data["eingereicht"] = "Ja"
                                    else:
                                        data["eingereicht"] = "Nein"
                                
                                elif "Eingereicht" in key and "Zeitpunkt" not in key:
                                    # This is the submission date, skip as per user request
                                    pass
                                
                                elif "Antragsteller" in key:
                                    # Extract main applicant name (before KV info, "und X weitere", or "dort beschlossen")
                                    # Value examples:
                                    #   "Kreisversammlung KV Neuburg-Schrobenhausen (dort beschlossen am: 11.06.2026)"
                                    #   "Bundesvorstand (dort beschlossen am: 05.01.2026)"
                                    #   "Gerdt Philipp Uhlhorn (KV Siegen-Wittgenstein) und 3 weitere Antragsteller*innen (Frauenanteil: 0%)"
                                    #   "Katharina Helling (KV München) und 13 weitere Antragsteller*innen (Frauenanteil: 21%)"
                                    #   "Carolin Astrid Rennerund 91 weitere Antragsteller*innen (Frauenanteil: 63%)" (no space before "und")
                                    # We want just the name/org part before any of these markers
                                    # Handle both " und " and "und" (no space) cases
                                    antragssteller_match = re.match(r"^(.+?)(?:\s*\(dort beschlossen am:|\s*\(KV\s+|\s+und\s+\d+\s+weitere|(?<=[a-zäöüß])und\s+\d+\s+weitere|$)", value, re.IGNORECASE)
                                    if antragssteller_match:
                                        data["antragssteller"] = antragssteller_match.group(1).strip()
                                    else:
                                        # Fallback: take everything before first parenthesis or "und"
                                        fallback_match = re.match(r"^([^(]+?)(?:\s*\(|\s+und\s+\d+|(?<=[a-zäöüß])und\s+\d+|$)", value, re.IGNORECASE)
                                        if fallback_match:
                                            data["antragssteller"] = fallback_match.group(1).strip()
                                    
                                    # Extract "und X weitere Antragsteller*innen" count
                                    # Handle both " und " and "und" (no space) cases
                                    weitere_match = re.search(r"(?:\s+und|(?<=[a-zäöüß])und)\s*(\d+)\s+weitere\s+Antragsteller", value, re.IGNORECASE)
                                    if weitere_match:
                                        # Add 1 for the main applicant
                                        data["anzahlAntragssteller"] = int(weitere_match.group(1)) + 1
                                    # Extract Frauenanteil from "Frauenanteil: X%"
                                    frauen_match = re.search(r"Frauenanteil:\s*(\d+)%", value)
                                    if frauen_match:
                                        data["frauenanteil"] = f"{frauen_match.group(1)}%"
                                    # Try to extract "dort beschlossen am:" as Zeitpunkt Angelegt
                                    beschlossen_match = re.search(r"dort beschlossen am:\s*(\d{2}\.\d{2}\.\d{4})", value)
                                    if beschlossen_match:
                                        data["zeitpunktAngelegt"] = beschlossen_match.group(1)
                                
                                elif "Angelegt" in key:
                                    # Extract date from "Angelegt: DD.MM.YYYY, HH:MM"
                                    angelegt_match = re.search(r"(\d{2}\.\d{2}\.\d{4})", value)
                                    if angelegt_match:
                                        data["zeitpunktAngelegt"] = angelegt_match.group(1)

                    # Extract Anzahl Antragssteller and Frauenanteil from supporters section (fallback)
                    supporters_section = soup.find(class_="supporters")
                    if supporters_section:
                        # First try to get from moreSupporters div in the table
                        more_supporters = soup.find(class_="moreSupporters")
                        if more_supporters:
                            more_text = more_supporters.get_text()
                            weitere_match = re.search(r"und\s+(\d+)\s+weitere\s+Antragsteller", more_text, re.IGNORECASE)
                            if weitere_match and not data["anzahlAntragssteller"]:
                                data["anzahlAntragssteller"] = int(weitere_match.group(1)) + 1
                            frauen_match = re.search(r"Frauenanteil:\s*(\d+)%", more_text)
                            if frauen_match and not data["frauenanteil"]:
                                data["frauenanteil"] = f"{frauen_match.group(1)}%"
                        
                        # Count supporters from list items as fallback
                        supporter_items = supporters_section.find_all("li")
                        if supporter_items and not data["anzahlAntragssteller"]:
                            data["anzahlAntragssteller"] = len(supporter_items) + 1  # +1 for main applicant
                        elif supporter_items and data["anzahlAntragssteller"]:
                            # Verify count matches
                            pass
                        else:
                            # Try to find count in text
                            supporter_text = supporters_section.get_text()
                            if "Nur zugelassene Gruppen" in supporter_text and not data["anzahlAntragssteller"]:
                                data["anzahlAntragssteller"] = 0
                    
                    # Extract Frauenanteil from page text as final fallback
                    if not data["frauenanteil"]:
                        all_text = soup.get_text()
                        frauenanteil_match = re.search(r'Frauenanteil:\s*(\d+)%', all_text)
                        if frauenanteil_match:
                            data["frauenanteil"] = f"{frauenanteil_match.group(1)}%"
                    
                    # Extract Zeitpunkt Angelegt from page text if not found in table
                    if not data["zeitpunktAngelegt"]:
                        all_text = soup.get_text()
                        beschlossen_match = re.search(r'(?:dort beschlossen am:|Angelegt:)\s*(\d{2}\.\d{2}\.\d{4})', all_text)
                        if beschlossen_match:
                            data["zeitpunktAngelegt"] = beschlossen_match.group(1)

                # Extract title for additional context
                title_tag = soup.find("h1")
                if title_tag:
                    data["title"] = title_tag.get_text(strip=True)

            except Exception:
                pass

        # For non-readable amendments without breadcrumb, fetch motion prefix from motion page
        if not data["motionPrefix"] and data.get("readable") == "False" and data.get("motionSlug"):
            data["motionPrefix"] = self._fetch_motion_prefix(data["motionSlug"])

        # Fallback: extract amendmentId from filename
        if not data["amendmentId"]:
            filename_match = re.search(r"-(\d+)-\d{8}-\d{4}\.txt$", file_path.name)
            if filename_match:
                data["amendmentId"] = filename_match.group(1)

        # Set Zuletzt aktualisiert to current timestamp
        data["zuletztAktualisiert"] = datetime.now().isoformat()

        return data

    def _read_existing_notes(self, worksheet) -> Dict[str, str]:
        """
        Read existing notes from the worksheet to preserve them.
        
        Args:
            worksheet: The gspread worksheet object
            
        Returns:
            Dictionary mapping amendmentId to note text
        """
        notes = {}
        try:
            all_values = worksheet.get_all_values()
            if not all_values:
                return notes
            
            headers = all_values[0]
            notizen_idx = None
            for i, header in enumerate(headers):
                if header.lower() == "notizen":
                    notizen_idx = i
                    break
            
            if notizen_idx is None:
                return notes
            
            for row in all_values[1:]:
                if len(row) > notizen_idx:
                    amendment_id = row[0] if row else ""
                    note = row[notizen_idx] if len(row) > notizen_idx else ""
                    if amendment_id and note:
                        notes[amendment_id] = note
                        
        except Exception as e:
            print(f"[SheetsExporter] Error reading existing notes: {e}")
        
        return notes

    def export_to_sheet(self, output_dir: Path) -> bool:
        if not self.service_account_file.exists():
            print(f"[SheetsExporter] Service account file not found: {self.service_account_file}")
            return False

        # Find all amendment files
        amendment_files = list(output_dir.glob("*-*-*.txt"))
        if not amendment_files:
            print("[SheetsExporter] No amendment files found to export")
            return False

        # Parse all files
        rows_dict = {}  # amendmentId -> row data (deduplicate)
        for file_path in amendment_files:
            if file_path.name.startswith("motion-") or file_path.name == "amendment_urls.txt":
                continue
            data = self._parse_amendment_file(file_path)
            if data:
                amendment_id = data.get("amendmentId", "")
                if amendment_id:
                    # Keep the first (latest by sort order) or overwrite - deduplicate by amendmentId
                    row_data = [
                        amendment_id,
                        data.get("url", ""),
                        data.get("motionPrefix", ""),
                        data.get("eingereicht", ""),
                        data.get("anzahlAntragssteller", ""),
                        data.get("frauenanteil", ""),
                        data.get("status", ""),
                        data.get("antragssteller", ""),
                        data.get("zeitpunktAngelegt", ""),
                        data.get("zuletztAktualisiert", ""),
                        "",  # Notizen - will be filled from existing data
                    ]
                    rows_dict[amendment_id] = row_data

        if not rows_dict:
            print("[SheetsExporter] No valid amendment data to export")
            return False

        # Convert to list and sort by amendmentId descending (largest ID first)
        rows = list(rows_dict.values())
        rows.sort(key=lambda r: int(r[0]) if r[0].isdigit() else 0, reverse=True)

        # Authenticate and export
        client = self._authenticate()
        if not client:
            return False

        try:
            spreadsheet = client.open_by_key(self.spreadsheet_id)
            worksheet = spreadsheet.worksheet(self.worksheet_name)

            # Read existing notes before updating
            existing_notes = self._read_existing_notes(worksheet)

            # Prepare data rows with preserved notes
            data_rows = []
            for row in rows:
                amendment_id = str(row[0])
                # Preserve existing note if available - replace index 10 (Notizen)
                if amendment_id in existing_notes:
                    row[10] = existing_notes[amendment_id]
                else:
                    row[10] = ""
                data_rows.append(row)

            if data_rows:
                # Clear existing data rows first (preserve header row 1)
                # Get current sheet dimensions to clear all old data
                try:
                    all_values = worksheet.get_all_values()
                    if len(all_values) > 1:
                        last_row = len(all_values)
                        # Clear from A2 to K{last_row}
                        worksheet.batch_clear([f"A2:K{last_row}"])
                except Exception:
                    pass  # If clear fails, continue with update (will overwrite)
                
                # Write new data starting from row 2
                worksheet.update(f"A2:K{len(data_rows) + 1}", data_rows)

            print(f"[SheetsExporter] Exported {len(rows)} amendments to Google Sheets")
            return True

        except Exception as e:
            import traceback
            print(f"[SheetsExporter] Error exporting to sheet:")
            traceback.print_exc()
            return False
