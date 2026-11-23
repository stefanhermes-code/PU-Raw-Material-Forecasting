from __future__ import annotations
import re
from app.sources.connectors.public_tdi import PublicTDIExcerptConnector

class PUDailyTDIConnector(PublicTDIExcerptConnector):
    """PUdaily relaunched its site in 2026: the old en.pudaily.com host is decommissioned
    (its TLS certificate no longer matches, hence the CERTIFICATE_VERIFY_FAILED failures) and
    all TDI prices now sit behind "Customer Login" on www.pudaily.com. Without an HTC
    subscription this source is monitor-only: the public page is fetched and stored as
    evidence, and the parser keeps its shape in case a public excerpt reappears.
    Verified in a real browser on 2026-09-08."""
    source_id = "pudaily_tdi"
    source_url = "https://www.pudaily.com/Home/PriceIntelligence"
    def extract_html(self, html: str, source_url: str):
        text = self.visible_text(html)
        m = re.search(r"TDI(?:\s+80/20)?\s+(?:(East China|North China|South China|China)\s+)?(20\d{2}-\d{2}-\d{2})\s+([0-9]{4,6}(?:\.[0-9]+)?)\s+(CNY|RMB|USD)/(?:MT|TON)", text, re.I)
        if not m: return []
        grade = "TDI 80/20" if re.search(r"TDI\s+80/20", text, re.I) else None
        return [self.record(source_url, price=float(m.group(3)), dt=self.iso_date(m.group(2)), geography=m.group(1) or "China", currency="USD" if m.group(4).upper()=="USD" else "CNY", grade=grade)]
