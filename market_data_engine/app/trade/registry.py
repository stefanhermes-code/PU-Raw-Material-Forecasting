"""Source definitions for the trade layer, one per Eurostat material and per Comtrade window."""
from __future__ import annotations

import calendar
from datetime import date, datetime, timezone

from app.domain.enums import RightsStatus
from app.sources.registry import SourceDefinition
from app.trade import materials as m

COMEXT_URL = "https://ec.europa.eu/eurostat/api/comext/dissemination/statistics/1.0/data/DS-045409"
COMTRADE_URL = "https://comtradeapi.un.org/public/v1/preview/C"

# UN Comtrade windows. The background collector allows a trade source 300 seconds (90 until
# 13 October 2026), and the public API one
# period per request at about one request every three to four seconds, so each source is a block the
# API can serve in that time: annual in blocks of years, monthly one calendar year per source.
# Closed blocks are re-read monthly for revisions and late weights; the open ones weekly.
COMTRADE_FIRST_YEAR = 2000
COMTRADE_FIRST_MONTHLY_YEAR = 2016
BILATERAL_FIRST_YEAR = 2015


def comtrade_windows(today: date | None = None) -> list[tuple]:
    """(source_id, frequency, first, last, refresh, kind) for every Comtrade source as of today."""
    today = today or datetime.now(timezone.utc).date()
    year = today.year
    windows: list[tuple] = [
        ("comtrade_annual_2000_2008", "A", "2000", "2008", "MONTHLY", "world"),
        ("comtrade_annual_2009_2017", "A", "2009", "2017", "MONTHLY", "world"),
        ("comtrade_annual_2018_onward", "A", "2018", None, "WEEKLY", "world"),
        ("comtrade_annual_bilateral", "A", str(BILATERAL_FIRST_YEAR), None, "WEEKLY", "bilateral"),
    ]
    for y in range(COMTRADE_FIRST_MONTHLY_YEAR, year + 1):
        windows.append((f"comtrade_monthly_{y}", "M", f"{y}01", f"{y}12",
                        "WEEKLY" if y >= year - 1 else "MONTHLY", "world"))
    return windows


def period_end(last: str | None) -> str | None:
    """The last day a window covers, read from "YYYY", "YYYYMM" or "YYYY-MM".

    `comtrade_windows` has always known which blocks are closed - "2008", "201612" - and which run
    on, and that is the difference between an archive that is complete and a feed that has stopped.
    Nothing carried it out of this module, so the application judged `comtrade_monthly_2016` by how
    far 2016 is from today and called it broken, every day, for ever.
    """
    text = str(last or "").replace("-", "")
    if len(text) == 4 and text.isdigit():
        return f"{text}-12-31"
    if len(text) == 6 and text.isdigit():
        year, month = int(text[:4]), int(text[4:6])
        if 1 <= month <= 12:
            return f"{year:04d}-{month:02d}-{calendar.monthrange(year, month)[1]:02d}"
    return None


def trade_source_definitions() -> list[SourceDefinition]:
    approved = RightsStatus.APPROVED
    out: list[SourceDefinition] = []
    for material in m.MATERIALS:
        flows = "extra-EU imports and exports"
        partners = ", ".join(material.import_partners)
        out.append(SourceDefinition(
            m.eurostat_source_id(material), "Eurostat Comext",
            f"EU {material.product} Import and Export Unit Values (CN {material.cn})",
            COMEXT_URL, "PUBLIC_ATTRIBUTION", approved,
            collection_method="API", refresh_frequency="WEEKLY", expected_publish_frequency="MONTHLY",
            acquisition_methods=("HTML",), reliability_level=3,
            notes=(f"EU trade layer. Eurostat Comext DS-045409, CN {material.cn}, EU27 {flows}"
                   + (f", import partner series {partners} and the derived residual" if partners else "")
                   + ". Declared invoice values, stored as market prices (extra-EU total imports CIF, exports FOB); partner splits kept as variables. Checked weekly; monthly data."
                   + (f" Limitation: {material.limitation}" if material.limitation else "")
                   + " Registered 2026-10-13."),
            collector_status="STRUCTURED_COLLECTOR"))
        if material.eurostat_predecessor:
            spec = material.eurostat_predecessor
            out.append(SourceDefinition(
                m.eurostat_predecessor_source_id(material), "Eurostat Comext",
                f"EU {material.product} Import and Export Unit Values, predecessor code (CN {spec.cn}, to {spec.valid_to})",
                COMEXT_URL, "PUBLIC_ATTRIBUTION", approved,
                collection_method="API", refresh_frequency="MONTHLY", expected_publish_frequency="HISTORICAL",
                acquisition_methods=("HTML",), reliability_level=3,
                notes=f"Predecessor series, held apart from CN {material.cn}. Mapping: {spec.mapping_status}. {spec.note}",
                collector_status="STRUCTURED_COLLECTOR",
                covers_until=period_end(spec.valid_to)))
    for source_id, frequency, first, last, refresh, kind in comtrade_windows():
        bilateral = kind == "bilateral"
        window = f"{first}-{last}" if last else f"{first} onward"
        out.append(SourceDefinition(
            source_id, "UN Comtrade",
            f"Global trade layer - {'annual' if frequency == 'A' else 'monthly'} HS6, {window}",
            COMTRADE_URL, "PUBLIC_ATTRIBUTION", approved,
            collection_method="API", refresh_frequency=refresh,
            expected_publish_frequency="ANNUAL" if frequency == "A" else "MONTHLY",
            acquisition_methods=("HTML",), reliability_level=3,
            notes=("Global trade layer. UN Comtrade public API: reporters "
                   + ", ".join(m.REPORTERS.values()) + "; partner World"
                   + ("; bilateral exports of polyether polyol to EU member states only" if bilateral else "")
                   + ". USD per tonne of net weight; n/r where the weight is missing. Comtrade EU figures are"
                     " not used - Eurostat is authoritative for EU27. Registered 2026-10-13."),
            collector_status="STRUCTURED_COLLECTOR",
            covers_until=period_end(last)))
    return out


def comtrade_window(source_id: str):
    for window in comtrade_windows():
        if window[0] == source_id:
            return window
    return None
