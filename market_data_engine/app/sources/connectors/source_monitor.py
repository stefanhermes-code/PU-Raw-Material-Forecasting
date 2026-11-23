from __future__ import annotations

import base64
import html

import httpx

from app.domain.schemas import SourceItem
from app.sources.registry import SourceDefinition


class SourceMonitorConnector:
    """Daily source-presence check with rendered screenshot fallback.

    This connector intentionally emits no market observations. It keeps a source
    under daily surveillance until a source-specific structured extractor is
    implemented or authorised credentials are configured.
    """

    allow_empty_parse = True

    def __init__(self, source: SourceDefinition, *, direct_fetch=None, browser_capture=None):
        self.source = source
        self.source_id = source.source_id
        self.source_url = source.base_url
        self._direct_fetch = direct_fetch or self._fetch_direct
        self._browser_capture = browser_capture or self._capture_browser

    def discover(self) -> list[SourceItem]:
        return [SourceItem(source_url=self.source_url)]

    @staticmethod
    def _fetch_direct(url: str) -> bytes:
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/152.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        response = httpx.get(url, headers=headers, follow_redirects=True, timeout=30.0)
        response.raise_for_status()
        return response.content

    @staticmethod
    def _capture_browser(url: str) -> tuple[str, bytes]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - deployment guard
            raise RuntimeError("playwright is required for screenshot fallback") from exc

        with sync_playwright() as playwright:  # pragma: no cover - live browser integration
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 1100}, locale="en-US")
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2000)
                return page.locator("body").inner_text(timeout=10000), page.screenshot(full_page=True, type="png")
            finally:
                browser.close()

    @staticmethod
    def _bundle(visible_text: str, screenshot: bytes) -> bytes:
        encoded = base64.b64encode(screenshot).decode("ascii")
        return (
            '<html><body data-acquisition="browser-rendered-monitor">'
            f"<pre>{html.escape(visible_text)}</pre>"
            f'<img data-evidence-screenshot="daily-monitor" src="data:image/png;base64,{encoded}" />'
            "</body></html>"
        ).encode("utf-8")

    def fetch_item(self, item: SourceItem) -> bytes:
        # Subscription/auth-oriented sources are monitored through the rendered
        # public page first. This captures what is visibly published without
        # attempting to bypass login or subscription controls.
        if self.source.requires_auth:
            try:
                visible_text, screenshot = self._browser_capture(item.source_url)
                return self._bundle(visible_text, screenshot)
            except Exception:
                payload = self._direct_fetch(item.source_url)
                if payload:
                    return payload
                raise

        try:
            payload = self._direct_fetch(item.source_url)
            if payload:
                return payload
        except Exception:
            pass
        visible_text, screenshot = self._browser_capture(item.source_url)
        return self._bundle(visible_text, screenshot)

    def extract_html(self, html_text: str, source_url: str):
        return []
