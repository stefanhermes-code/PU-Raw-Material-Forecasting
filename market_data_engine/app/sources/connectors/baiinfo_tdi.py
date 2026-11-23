from __future__ import annotations
import re
from app.sources.connectors.public_tdi import PublicTDIExcerptConnector

class BaiinfoTDIConnector(PublicTDIExcerptConnector):
    source_id = "baiinfo_tdi"
    source_url = "https://www.baiinfo.com/"
    def extract_html(self, html: str, source_url: str):
        text = self.visible_text(html)
        if "/verify/" in text.lower() or "验证码" in text: return []
        m = re.search(r"TDI\s+(?:(East China|North China|South China)\s+)?(20\d{2}-\d{2}-\d{2})\s+([0-9]{4,6}(?:\.[0-9]+)?)\s+(CNY|RMB|USD)/(?:MT|TON)", text, re.I)
        if not m: return []
        return [self.record(source_url, price=float(m.group(3)), dt=self.iso_date(m.group(2)), geography=m.group(1) or "China", currency="USD" if m.group(4).upper()=="USD" else "CNY")]
