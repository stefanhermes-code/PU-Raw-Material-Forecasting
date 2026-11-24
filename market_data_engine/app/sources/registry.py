from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import RightsStatus


@dataclass
class SourceDefinition:
    source_id: str
    publisher: str
    source_name: str
    base_url: str
    rights_class: str
    rights_status: RightsStatus
    collection_method: str = "HTML"
    refresh_frequency: str = "DAILY_CHECK"
    expected_publish_frequency: str = "DAILY"
    acquisition_methods: tuple[str, ...] = ("HTML", "RENDERED_BROWSER", "SCREENSHOT")
    requires_auth: bool = False
    mapping_status: str = "READY"
    active: bool = True
    reliability_level: int = 3
    notes: str = ""
    collector_status: str = "MONITOR_ONLY"
    # The last day this source's period covers, for a source that covers a fixed period and will
    # never print beyond it: a closed Comtrade year, a superseded customs code. `None` for an open
    # feed. `comtrade_windows` has recorded each block's last year since the trade layer was
    # written and the fact stopped there, so a reader of this endpoint had no way to tell a
    # finished archive from a feed that died - and judged both on how old the newest reading was.
    covers_until: str | None = None


def _trade_layer_definitions() -> list[SourceDefinition]:
    from app.trade.registry import trade_source_definitions
    return trade_source_definitions()


class SourceRegistry:
    def __init__(self, sources: list[SourceDefinition] | None = None):
        self._sources = {source.source_id: source for source in (sources or [])}

    @classmethod
    def default_with_fixture(cls):
        reg = cls.default()
        reg._sources["fixture_tdi"] = SourceDefinition(
            "fixture_tdi",
            "HTC Global",
            "Deterministic TDI fixture",
            "fixture://tdi-china",
            "INTERNAL_TEST",
            RightsStatus.APPROVED,
            collection_method="FIXTURE",
            refresh_frequency="MANUAL",
            expected_publish_frequency="MANUAL",
            acquisition_methods=("FIXTURE",),
            reliability_level=5,
            notes="Deterministic non-network fixture for end-to-end pipeline tests.",
        )
        return reg

    @classmethod
    def default(cls):
        approved = RightsStatus.APPROVED
        return cls(
            [
                SourceDefinition(
                    "sunsirs_tdi",
                    "SunSirs / 100ppi",
                    "SunSirs China TDI Spot Price",
                    "https://www.sunsirs.com/uk/prodetail-1095.html",
                    "PUBLIC_ATTRIBUTION",
                    approved,
                    collection_method="HTML",
                    expected_publish_frequency="DAILY",
                    acquisition_methods=("HTML", "RENDERED_BROWSER", "SCREENSHOT"),
                    reliability_level=3,
                    notes="Direct public China TDI spot-price table, plus the free English TDI news articles that feed the event lane. SunSirs is the engine's second *independent* event publisher (Longzhong is the first), which is what makes signal corroboration possible at all; it is NOT independent of 100ppi, which shares its lineage.",
                    collector_status="STRUCTURED_COLLECTOR",
                ),
                SourceDefinition(
                    "echemi_tdi",
                    "ECHEMI",
                    "ECHEMI TDI Market Price & Insight",
                    "https://www.echemi.com/productsInformation/pd20170215195139318-toluene-diisocyanate.html",
                    "LICENSED_INTERNAL_USE",
                    approved,
                    collection_method="HTML_PRODUCT_INFORMATION",
                    expected_publish_frequency="DAILY",
                    acquisition_methods=("HTML", "VISIBLE_DOM", "RENDERED_BROWSER", "SCREENSHOT"),
                    requires_auth=False,
                    reliability_level=4,
                    notes="Internal-use ingestion from the visible TDI product-information page; China Domestic is governed as EXW. International rows retain their stated Incoterm. Regional/enterprise rows require a real Incoterm before price-database ingestion. ECHEMI began serving its access-verification page to every request on 15 September 2026. The last reading we took is 11 September; the 228 runs since have all been turned away. The gate is not worked around (Stefan's standing rule), so the source is monitor-only until it opens or a subscription exists - registered, not fetched. Retired as a price source on 16 October 2026 (Stefan's call): across six markets it supplied 24 readings of 20,710 and was never the only publisher on any of them, while the gate has turned away every run since 15 September. The registration and its readings stay - the readings were re-read off the page's own table in migration 0030 and agree with Longzhong to within 0.9%, and two series curation had already cut are named in series_curation. ECHEMI's news lane is unaffected.",
                    collector_status="MONITOR_ONLY",
                active=False,
                ),
                SourceDefinition(
                    "longzhong_tdi",
                    "Longzhong / Oilchem",
                    "Longzhong TDI national market average",
                    "https://list.oilchem.net/sem/392/7.html",
                    "PUBLIC_ATTRIBUTION",
                    approved,
                    collection_method="HTML_TABLE",
                    expected_publish_frequency="DAILY",
                    acquisition_methods=("HTML_TABLE", "HTML_ARTICLE", "RENDERED_BROWSER", "SCREENSHOT"),
                    reliability_level=4,
                    notes="Public national TDI market-average table; preserve source URL and attribution.",
                    collector_status="STRUCTURED_COLLECTOR",
                ),
                SourceDefinition(
                    "sci99_tdi",
                    "SCI99 / Zhuochuang",
                    "SCI99 TDI market intelligence",
                    "https://www.sci99.com/monitor-375-0.html",
                    "LICENSED_OR_PUBLIC_EXCERPT",
                    approved,
                    collection_method="ACQUISITION_LADDER",
                    expected_publish_frequency="INTRADAY_DAILY_WEEKLY",
                    acquisition_methods=("DOWNLOAD", "HTML", "RENDERED_BROWSER", "SCREENSHOT"),
                    requires_auth=True,
                    mapping_status="PUBLIC_MONITORING",
                    reliability_level=4,
                    collector_status="VISIBLE_EXCERPT_COLLECTOR",
                ),
                SourceDefinition(
                    "baiinfo_tdi",
                    "Baiinfo",
                    "Baiinfo TDI market intelligence",
                    "https://www.baiinfo.com/",
                    "LICENSED_OR_PUBLIC_EXCERPT",
                    approved,
                    collection_method="ACQUISITION_LADDER",
                    expected_publish_frequency="DAILY_WEEKLY",
                    acquisition_methods=("DOWNLOAD", "HTML", "RENDERED_BROWSER", "SCREENSHOT"),
                    requires_auth=True,
                    mapping_status="PUBLIC_MONITORING",
                    reliability_level=4,
                    notes="baiinfo.com serves a slider human-check to every new visitor and all prices are behind a paid login; no HTC subscription. Monitor-only (verified 2026-09-08).",
                    collector_status="MONITOR_ONLY",
                ),
                SourceDefinition(
                    "pudaily_tdi",
                    "PUdaily",
                    "PUdaily Price Intelligence",
                    "https://www.pudaily.com/Home/PriceIntelligence",
                    "LICENSED_INTERNAL_USE",
                    approved,
                    collection_method="ACQUISITION_LADDER",
                    expected_publish_frequency="DAILY_WEEKLY",
                    acquisition_methods=("DOWNLOAD", "RENDERED_BROWSER", "VISIBLE_DOM", "SCREENSHOT"),
                    requires_auth=True,
                    mapping_status="PUBLIC_MONITORING",
                    reliability_level=4,
                    notes="Site relaunched 2026 (old en.pudaily.com host decommissioned). All TDI prices behind Customer Login; no HTC subscription, so monitor-only until one exists.",
                    collector_status="MONITOR_ONLY",
                ),
                SourceDefinition(
                    "chemanalyst_tdi",
                    "ChemAnalyst",
                    "ChemAnalyst TDI pricing",
                    "https://www.chemanalyst.com/Pricing-data/toluene-diisocyanate-35",
                    "LICENSED_OR_PUBLIC_EXCERPT",
                    approved,
                    collection_method="ACQUISITION_LADDER",
                    expected_publish_frequency="REALTIME_QUARTERLY_NEWS",
                    acquisition_methods=("HTML", "RENDERED_BROWSER", "SCREENSHOT"),
                    requires_auth=True,
                    mapping_status="PUBLIC_MONITORING",
                    reliability_level=3,
                    notes="Quarterly regional averages read from the free page, verified 2026-09-08. "
                          "ChemAnalyst began answering 403 Forbidden on 13 September 2026, the day "
                          "after our last reading, and had turned away 178 consecutive runs by "
                          "5 November 2026 - the same fortnight ECHEMI closed its own doors, and "
                          "the same shape. The declared ladder lists RENDERED_BROWSER and "
                          "SCREENSHOT above HTML, but a 403 is the publisher saying no, and the "
                          "gate is not worked around (Stefan's standing rule). So monitor-only "
                          "until the page opens again or a subscription exists - registered, not "
                          "fetched. It leaves TDI Japan and TDI Germany with no live feed; both "
                          "were quarterly averages from this page alone.",
                    collector_status="MONITOR_ONLY",
                ),
                SourceDefinition(
                    "pricepedia_pu",
                    "PricePedia",
                    "PricePedia polyurethane market intelligence",
                    "https://www.pricepedia.it/en/magazine/archive/",
                    "PUBLIC_ATTRIBUTION",
                    approved,
                    collection_method="HTML_ARTICLE_ARCHIVE",
                    expected_publish_frequency="EVENT_DRIVEN",
                    acquisition_methods=("HTML", "RENDERED_BROWSER", "SCREENSHOT"),
                    requires_auth=False,
                    mapping_status="MARKET_INTELLIGENCE",
                    reliability_level=4,
                    notes=(
                        "Use PU-relevant articles as feature/event evidence. Explicit price observations may be "
                        "used for corroboration only after exact commercial-identity qualification; do not feed "
                        "customs-derived unit values directly into the Current Market Reference."
                    ),
                    collector_status="MONITOR_ONLY",
                ),
                SourceDefinition(
                    "ki_pie_tdi",
                    "Kunststoff Information / PIE",
                    "KI/PIE polyurethane pricing",
                    "https://www.kiweb.de/Default.aspx?pageid=205",
                    "LICENSED_INTERNAL_USE",
                    approved,
                    collection_method="ACQUISITION_LADDER",
                    expected_publish_frequency="MONTHLY_EVENT_DRIVEN",
                    acquisition_methods=("AUTHENTICATED_BROWSER", "HTML", "SCREENSHOT"),
                    requires_auth=True,
                    mapping_status="PUBLIC_MONITORING",
                    reliability_level=4,
                ),
                SourceDefinition(
                    "argus_isocyanates",
                    "Argus Media",
                    "Argus Isocyanates",
                    "https://www.argusmedia.com/en/solutions/products/argus-isocyanates/",
                    "LICENSED_INTERNAL_USE",
                    approved,
                    collection_method="ACQUISITION_LADDER",
                    expected_publish_frequency="WEEKLY_REALTIME_NEWS",
                    acquisition_methods=("DOWNLOAD_PDF", "AUTHENTICATED_BROWSER", "DOWNLOAD", "SCREENSHOT"),
                    requires_auth=True,
                    mapping_status="PUBLIC_MONITORING",
                    reliability_level=5,
                ),
                SourceDefinition(
                    "icis_isocyanates",
                    "ICIS",
                    "ICIS Isocyanates Price Report",
                    "https://www.icis.com/compliance/reports/isocyanates-price-report/",
                    "LICENSED_INTERNAL_USE",
                    approved,
                    collection_method="ACQUISITION_LADDER",
                    expected_publish_frequency="WEEKLY",
                    acquisition_methods=("DOWNLOAD_REPORT", "AUTHENTICATED_BROWSER", "HTML", "SCREENSHOT"),
                    requires_auth=True,
                    mapping_status="PUBLIC_MONITORING",
                    reliability_level=5,
                ),
                SourceDefinition(
                    "resourcewise_pu",
                    "Tecnon OrbiChem / ResourceWise",
                    "ResourceWise Polyurethanes & Intermediates",
                    "https://www.resourcewise.com/solutions/commodity-pricing/chemicals",
                    "LICENSED_INTERNAL_USE",
                    approved,
                    collection_method="ACQUISITION_LADDER",
                    expected_publish_frequency="DAILY_TO_MONTHLY",
                    acquisition_methods=("DOWNLOAD", "AUTHENTICATED_BROWSER", "DASHBOARD_EXPORT", "SCREENSHOT"),
                    requires_auth=True,
                    mapping_status="PUBLIC_MONITORING",
                    reliability_level=5,
                ),
                SourceDefinition(
                    "cma_pu_feedstocks",
                    "Chemical Market Analytics / OPIS",
                    "CMA Global Polyurethane Feedstocks",
                    "https://chemicalmarketanalytics.com/products/mas-global-polyurethane-feedstocks/",
                    "LICENSED_INTERNAL_USE",
                    approved,
                    collection_method="ACQUISITION_LADDER",
                    expected_publish_frequency="WEEKLY_MONTHLY_QUARTERLY",
                    acquisition_methods=("DOWNLOAD_EXCEL", "AUTHENTICATED_BROWSER", "DASHBOARD_EXPORT", "DOWNLOAD_PDF", "SCREENSHOT"),
                    requires_auth=True,
                    mapping_status="PUBLIC_MONITORING",
                    reliability_level=5,
                ),
                SourceDefinition(
                    "businessanalytiq_tdi",
                    "Business Analytiq",
                    "Business Analytiq TDI regional price index",
                    "https://businessanalytiq.com/procurementanalytics/index/toluene-diisocyanate-price-index/",
                    "PUBLIC_ATTRIBUTION",
                    approved,
                    collection_method="HTML",
                    expected_publish_frequency="MONTHLY",
                    acquisition_methods=("HTML", "RENDERED_BROWSER"),
                    reliability_level=3,
                    notes="Free monthly regional TDI price assessments in US$/kg for North America, Europe, Northeast Asia, Southeast Asia and Middle East. No Incoterm is stated by the publisher, so the basis is recorded as REGIONAL_MARKET. Verified 2026-09-08.",
                    collector_status="STRUCTURED_COLLECTOR",
                ),
                SourceDefinition(
                    "imarc_tdi",
                    "IMARC Group",
                    "IMARC TDI regional price report",
                    "https://www.imarcgroup.com/toluene-diisocyanate-pricing-report",
                    "PUBLIC_ATTRIBUTION",
                    approved,
                    collection_method="HTML",
                    expected_publish_frequency="MONTHLY",
                    acquisition_methods=("HTML", "RENDERED_BROWSER"),
                    reliability_level=3,
                    notes="Free monthly regional TDI prices in US$/kg (Europe, Northeast Asia, Southeast Asia, North America, Middle East) plus dated quarterly commentary. No Incoterm or grade stated, so the basis is REGIONAL_MARKET. Verified 2026-09-21.",
                    collector_status="STRUCTURED_COLLECTOR",
                ),
                SourceDefinition(
                    "imarc_mdi",
                    "IMARC Group",
                    "IMARC MDI regional price report",
                    "https://www.imarcgroup.com/methylene-diphenyl-diisocyanate-pricing-report",
                    "PUBLIC_ATTRIBUTION",
                    approved,
                    collection_method="HTML",
                    expected_publish_frequency="MONTHLY",
                    acquisition_methods=("HTML", "RENDERED_BROWSER"),
                    reliability_level=3,
                    notes="Free monthly regional MDI prices in US$/kg, filed as polymeric MDI: in flexible foam MDI means pMDI (Stefan, 2026-09-22). No Incoterm stated: REGIONAL_MARKET. Verified 2026-09-21.",
                    collector_status="STRUCTURED_COLLECTOR",
                ),
                # The feedstocks, from the same two free publishers that already carry TDI and MDI.
                #
                # Stefan, 1 October 2026, after I had argued the feedstocks were secondary on the
                # strength of Clint Raine's 2016 finding that crude explains ten to fifteen per cent
                # of an isocyanate price: "I know what Clint said, but do not forget his hypothesis
                # testing took place a long time [ago]. I compare it with the hole in the Ozon which
                # we did not know of until we had the capability to see it through more advanced
                # telescopes." The market has been through an energy shock and a Chinese capacity
                # build since, the question is the feedstock's own price rather than crude's, and we
                # now test a relationship properly. So we collect and let the measurement answer.
                #
                # Europe is the point of these: every feedstock we held before today is Chinese, and
                # a European producer buys European benzene, toluene and propylene oxide. Both
                # publishers print every region on one page, so the Asian and American figures come
                # with them and give the Chinese series a second publisher.
                SourceDefinition(
                    "imarc_benzene",
                    "IMARC Group",
                    "IMARC benzene regional price report",
                    "https://www.imarcgroup.com/benzene-pricing-report",
                    "PUBLIC_ATTRIBUTION",
                    approved,
                    collection_method="HTML",
                    expected_publish_frequency="MONTHLY",
                    acquisition_methods=("HTML", "RENDERED_BROWSER"),
                    reliability_level=3,
                    notes="Free monthly regional benzene prices in US$/kg, same page template as the TDI and MDI reports. Benzene is the feedstock of aniline and so of MDI. No Incoterm stated: REGIONAL_MARKET. Registered 2026-10-01.",
                    collector_status="STRUCTURED_COLLECTOR",
                ),
                SourceDefinition(
                    "imarc_toluene",
                    "IMARC Group",
                    "IMARC toluene regional price report",
                    "https://www.imarcgroup.com/toluene-pricing-report",
                    "PUBLIC_ATTRIBUTION",
                    approved,
                    collection_method="HTML",
                    expected_publish_frequency="MONTHLY",
                    acquisition_methods=("HTML", "RENDERED_BROWSER"),
                    reliability_level=3,
                    notes="Free monthly regional toluene prices in US$/kg. Toluene is the feedstock of TDI. No Incoterm stated: REGIONAL_MARKET. Registered 2026-10-01.",
                    collector_status="STRUCTURED_COLLECTOR",
                ),
                SourceDefinition(
                    "imarc_propylene_oxide",
                    "IMARC Group",
                    "IMARC propylene oxide regional price report",
                    "https://www.imarcgroup.com/propylene-oxide-pricing-report",
                    "PUBLIC_ATTRIBUTION",
                    approved,
                    collection_method="HTML",
                    expected_publish_frequency="MONTHLY",
                    acquisition_methods=("HTML", "RENDERED_BROWSER"),
                    reliability_level=3,
                    notes="Free monthly regional propylene oxide prices in US$/kg. PO is the feedstock of polyether polyol. No Incoterm stated: REGIONAL_MARKET. Registered 2026-10-01.",
                    collector_status="STRUCTURED_COLLECTOR",
                ),
                SourceDefinition(
                    "imarc_adipic_acid",
                    "IMARC Group",
                    "IMARC adipic acid regional price report",
                    "https://www.imarcgroup.com/adipic-acid-pricing-report",
                    "PUBLIC_ATTRIBUTION",
                    approved,
                    collection_method="HTML",
                    expected_publish_frequency="MONTHLY",
                    acquisition_methods=("HTML", "RENDERED_BROWSER"),
                    reliability_level=3,
                    notes="Free monthly regional adipic acid prices in US$/kg. Adipic acid is a feedstock of aliphatic polyester polyol. Read 2026-10-02: September 2026 printed for Europe, North America and Northeast Asia, and Business Analytiq printed the same three within 3.3 percent. No Incoterm stated: REGIONAL_MARKET. Registered 2026-10-02.",
                    collector_status="STRUCTURED_COLLECTOR",
                ),
                SourceDefinition(
                    "businessanalytiq_benzene",
                    "Business Analytiq",
                    "Business Analytiq benzene price index",
                    "https://businessanalytiq.com/procurementanalytics/index/benzene-price-index/",
                    "PUBLIC_ATTRIBUTION",
                    approved,
                    collection_method="HTML",
                    expected_publish_frequency="MONTHLY",
                    acquisition_methods=("HTML", "RENDERED_BROWSER"),
                    reliability_level=3,
                    notes="Free monthly regional benzene price index in US$/kg, seven regions. The second publisher of the European benzene figure, so the market can be corroborated rather than taken from one page. No Incoterm stated: REGIONAL_MARKET. Registered 2026-10-01.",
                    collector_status="STRUCTURED_COLLECTOR",
                ),
                SourceDefinition(
                    "businessanalytiq_toluene",
                    "Business Analytiq",
                    "Business Analytiq toluene price index",
                    "https://businessanalytiq.com/procurementanalytics/index/toluene-price-index/",
                    "PUBLIC_ATTRIBUTION",
                    approved,
                    collection_method="HTML",
                    expected_publish_frequency="MONTHLY",
                    acquisition_methods=("HTML", "RENDERED_BROWSER"),
                    reliability_level=3,
                    notes="Free monthly regional toluene price index in US$/kg. The second publisher of the European toluene figure. No Incoterm stated: REGIONAL_MARKET. Registered 2026-10-01.",
                    collector_status="STRUCTURED_COLLECTOR",
                ),
                SourceDefinition(
                    "businessanalytiq_propylene_oxide",
                    "Business Analytiq",
                    "Business Analytiq propylene oxide price index",
                    "https://businessanalytiq.com/procurementanalytics/index/propylene-oxide-price-index/",
                    "PUBLIC_ATTRIBUTION",
                    approved,
                    collection_method="HTML",
                    expected_publish_frequency="MONTHLY",
                    acquisition_methods=("HTML", "RENDERED_BROWSER"),
                    reliability_level=3,
                    notes="Free monthly regional propylene oxide price index in US$/kg. The second publisher of the European PO figure. No Incoterm stated: REGIONAL_MARKET. Registered 2026-10-01.",
                    collector_status="STRUCTURED_COLLECTOR",
                ),
                # The customs record, for the history the publishers do not carry. Monthly EU import
                # unit values by CN8, back to 1988, free under Eurostat's own re-use policy. Stored
                # as trade_unit_value rather than as a price: it is what crossed the border, mixing
                # contract and spot, every grade under the code and intra-company transfers, so it
                # is evidence for the correlation work and reaches no customer screen.
                # CN codes verified against the 2024 nomenclature, 1 October 2026.
                SourceDefinition(
                    "businessanalytiq_adipic_acid",
                    "Business Analytiq",
                    "Business Analytiq adipic acid price index",
                    "https://businessanalytiq.com/procurementanalytics/index/adipic-acid-price-index/",
                    "PUBLIC_ATTRIBUTION",
                    approved,
                    collection_method="HTML",
                    expected_publish_frequency="MONTHLY",
                    acquisition_methods=("HTML", "RENDERED_BROWSER"),
                    reliability_level=3,
                    notes="Free monthly regional adipic acid prices in US$/kg, the second publisher on the same market. No Incoterm stated: REGIONAL_MARKET. Registered 2026-10-02.",
                    collector_status="STRUCTURED_COLLECTOR",
                ),
                SourceDefinition(
                    "businessanalytiq_pure_mdi",
                    "Business Analytiq",
                    "Business Analytiq pure MDI price index",
                    "https://businessanalytiq.com/procurementanalytics/index/methylene-diphenyl-diisocyanate-pure-price-index/",
                    "PUBLIC_ATTRIBUTION",
                    approved,
                    collection_method="HTML",
                    expected_publish_frequency="MONTHLY",
                    acquisition_methods=("HTML", "RENDERED_BROWSER"),
                    reliability_level=3,
                    notes="Free monthly regional pure MDI prices in US$/kg, same page template as the TDI index. Registered 2026-10-02 because pure MDI is on the customer chemical list and had no collected series at all: the five markets we hold come from the licensed history and stop in August 2023. Read 2026-10-02: September 2026 printed for North America, Europe, Northeast Asia and the Middle East. No Incoterm stated: REGIONAL_MARKET.",
                    collector_status="STRUCTURED_COLLECTOR",
                ),
                SourceDefinition(
                    "sci99_benzene",
                    "SCI99 / Zhuochuang",
                    "SCI99 China benzene spot price",
                    "https://www.sci99.com/monitor-377-0.html",
                    "LICENSED_OR_PUBLIC_EXCERPT",
                    approved,
                    collection_method="HTML",
                    expected_publish_frequency="DAILY",
                    acquisition_methods=("HTML",),
                    reliability_level=4,
                    notes="Second publisher on a market SunSirs carried alone. The monitor id was read from the discovery run of 2026-10-02, which also returned 375 for TDI - the id this engine's SCI99 TDI connector has used since September, so the ids are the right ones. The parse is verified by the first collection run. Petroleum benzene, not the hydrogenated grade: the discovery also found 804 for hydrogenated benzene and that is a different material. Registered 2026-10-02.",
                    collector_status="STRUCTURED_COLLECTOR",
                ),
                SourceDefinition(
                    "sci99_toluene",
                    "SCI99 / Zhuochuang",
                    "SCI99 China toluene spot price",
                    "https://www.sci99.com/monitor-374-0.html",
                    "LICENSED_OR_PUBLIC_EXCERPT",
                    approved,
                    collection_method="HTML",
                    expected_publish_frequency="DAILY",
                    acquisition_methods=("HTML",),
                    reliability_level=4,
                    notes="Second publisher on a market SunSirs carried alone. The monitor id was read from the discovery run of 2026-10-02, which also returned 375 for TDI - the id this engine's SCI99 TDI connector has used since September, so the ids are the right ones. The parse is verified by the first collection run. Toluene, not xylene at 445 and not TDI at 375. Registered 2026-10-02.",
                    collector_status="STRUCTURED_COLLECTOR",
                ),
                SourceDefinition(
                    "sci99_diethylene_glycol",
                    "SCI99 / Zhuochuang",
                    "SCI99 China diethylene glycol spot price",
                    "https://www.sci99.com/monitor-507-0.html",
                    "LICENSED_OR_PUBLIC_EXCERPT",
                    approved,
                    collection_method="HTML",
                    expected_publish_frequency="DAILY",
                    acquisition_methods=("HTML",),
                    reliability_level=4,
                    notes="Second publisher on a market SunSirs carried alone. The monitor id was read from the discovery run of 2026-10-02, which also returned 375 for TDI - the id this engine's SCI99 TDI connector has used since September, so the ids are the right ones. The parse is verified by the first collection run. Diethylene glycol, not the monobutyl ether the discovery also returned, which is a solvent. Registered 2026-10-02.",
                    collector_status="STRUCTURED_COLLECTOR",
                ),
                SourceDefinition(
                    "sci99_adipic_acid",
                    "SCI99 / Zhuochuang",
                    "SCI99 China adipic acid spot price",
                    "https://www.sci99.com/monitor-378-0.html",
                    "LICENSED_OR_PUBLIC_EXCERPT",
                    approved,
                    collection_method="HTML",
                    expected_publish_frequency="DAILY",
                    acquisition_methods=("HTML",),
                    reliability_level=4,
                    notes="Second publisher on a market SunSirs carried alone. The monitor id was read from the discovery run of 2026-10-02, which also returned 375 for TDI - the id this engine's SCI99 TDI connector has used since September, so the ids are the right ones. The parse is verified by the first collection run. Adipic acid. JLC lists it too and has not yet delivered one, so this is the second voice. Registered 2026-10-02.",
                    collector_status="STRUCTURED_COLLECTOR",
                ),
                SourceDefinition(
                    "sci99_ethylene_glycol",
                    "SCI99 / Zhuochuang",
                    "SCI99 China ethylene glycol spot price",
                    "https://www.sci99.com/monitor-367-0.html",
                    "LICENSED_OR_PUBLIC_EXCERPT",
                    approved,
                    collection_method="HTML",
                    expected_publish_frequency="DAILY",
                    acquisition_methods=("HTML",),
                    reliability_level=4,
                    notes="Polyester feedstock with no Chinese price until 24 November 2026: the only lanes were customs months two to three behind. Stefan: 'Add the missing China lanes for the polyester feedstocks.' Feeds both polyester polyols. The ONLY publisher on this lane for now: SunSirs (prodetail-222) read 6% below it on the same days, and two publishers that far apart average to a price neither printed. Which of them is the market is Stefan's call.",
                    collector_status="STRUCTURED_COLLECTOR",
                ),
                SourceDefinition(
                    "sci99_maleic_anhydride",
                    "SCI99 / Zhuochuang",
                    "SCI99 China maleic anhydride spot price",
                    "https://www.sci99.com/monitor-114763214-0.html",
                    "LICENSED_OR_PUBLIC_EXCERPT",
                    approved,
                    collection_method="HTML",
                    expected_publish_frequency="DAILY",
                    acquisition_methods=("HTML",),
                    reliability_level=4,
                    notes="Polyester feedstock with no Chinese price until 24 November 2026: the only lanes were customs months two to three behind. Stefan: 'Add the missing China lanes for the polyester feedstocks.' Aliphatic polyester feedstock. The ONLY publisher on this lane for now: SunSirs (prodetail-660) read 19% above it on the same days - 8,775 against 7,350 yuan - which is two different products or bases, not two views of one. Which is the market is Stefan's call.",
                    collector_status="STRUCTURED_COLLECTOR",
                ),
                SourceDefinition(
                    "sci99_phthalic_anhydride",
                    "SCI99 / Zhuochuang",
                    "SCI99 China phthalic anhydride spot price",
                    "https://www.sci99.com/monitor-94703214-0.html",
                    "LICENSED_OR_PUBLIC_EXCERPT",
                    approved,
                    collection_method="HTML",
                    expected_publish_frequency="DAILY",
                    acquisition_methods=("HTML",),
                    reliability_level=4,
                    notes="Polyester feedstock with no Chinese price until 24 November 2026: the only lanes were customs months two to three behind. Stefan: 'Add the missing China lanes for the polyester feedstocks.' Aromatic polyester feedstock. Agrees with SunSirs to 0.2%.",
                    collector_status="STRUCTURED_COLLECTOR",
                ),
                SourceDefinition(
                    "sunsirs_phthalic_anhydride", "SunSirs / 100ppi", "SunSirs China phthalic anhydride spot price",
                    "https://www.sunsirs.com/uk/prodetail-541.html", "PUBLIC_ATTRIBUTION", approved,
                    collection_method="HTML", expected_publish_frequency="DAILY",
                    acquisition_methods=("HTML", "RENDERED_BROWSER", "SCREENSHOT"), reliability_level=3,
                    notes="Polyester feedstock with no Chinese price until 24 November 2026: the only lanes were customs months two to three behind. Stefan: 'Add the missing China lanes for the polyester feedstocks.' SunSirs lists it as 'PA'; 9,200 yuan against SCI99's 9,183 for 苯酐 says it is phthalic anhydride and not a polyamide, which SunSirs lists as PA6 and PA66.",
                    collector_status="STRUCTURED_COLLECTOR",
                ),
                SourceDefinition(
                    "sci99_pta",
                    "SCI99 / Zhuochuang",
                    "SCI99 China PTA (terephthalic acid) spot price",
                    "https://www.sci99.com/monitor-368-0.html",
                    "LICENSED_OR_PUBLIC_EXCERPT",
                    approved,
                    collection_method="HTML",
                    expected_publish_frequency="DAILY",
                    acquisition_methods=("HTML",),
                    reliability_level=4,
                    notes="Polyester feedstock with no Chinese price until 24 November 2026: the only lanes were customs months two to three behind. Stefan: 'Add the missing China lanes for the polyester feedstocks.' Aromatic polyester feedstock. Within 3% of SunSirs.",
                    collector_status="STRUCTURED_COLLECTOR",
                ),
                SourceDefinition(
                    "sunsirs_pta", "SunSirs / 100ppi", "SunSirs China PTA (terephthalic acid) spot price",
                    "https://www.sunsirs.com/uk/prodetail-356.html", "PUBLIC_ATTRIBUTION", approved,
                    collection_method="HTML", expected_publish_frequency="DAILY",
                    acquisition_methods=("HTML", "RENDERED_BROWSER", "SCREENSHOT"), reliability_level=3,
                    notes="Polyester feedstock with no Chinese price until 24 November 2026: the only lanes were customs months two to three behind. Stefan: 'Add the missing China lanes for the polyester feedstocks.' SunSirs files PTA under Textile, not Chemical; the reader is told so.",
                    collector_status="STRUCTURED_COLLECTOR",
                ),
                SourceDefinition(
                    "procurementresource_tdi",
                    "Procurement Resource",
                    "Procurement Resource TDI price trends",
                    "https://www.procurementresource.com/resource-center/toluene-diisocyanate-price-trends",
                    "PUBLIC_ATTRIBUTION",
                    approved,
                    collection_method="HTML",
                    expected_publish_frequency="MONTHLY",
                    acquisition_methods=("HTML", "RENDERED_BROWSER"),
                    reliability_level=3,
                    notes="Free monthly TDI table by country with Incoterm, the same page shape as "
                          "the polyether one. Registered 5 November 2026 to replace ChemAnalyst, "
                          "which has answered 403 since 13 September and left TDI Germany with no "
                          "feed. Germany CIF is collected and stays TDI Germany, not folded into "
                          "Europe (Stefan, 2 October 2026). Monthly, where ChemAnalyst was a "
                          "quarterly average, and the market had four of those in total. Verified "
                          "against the live page 5 November 2026: August 2026 at USD 2,103.90/MT "
                          "and July at 1,987.32, both Germany CIF. The page also prints China FOB, "
                          "India CIF, USA CIF and Canada CIF, which are not collected - TDI China "
                          "is already priced EXW in CNY by three Chinese publishers, and the rest "
                          "are markets nobody has asked for. It carries no Japan row, and no free "
                          "publisher does; TDI Northeast Asia is the nearest live figure.",
                    collector_status="STRUCTURED_COLLECTOR",
                ),
                SourceDefinition(
                    "procurementresource_polyether",
                    "Procurement Resource",
                    "Procurement Resource polyether polyol price trends",
                    "https://www.procurementresource.com/resource-center/polyether-polyol-price-trends",
                    "PUBLIC_ATTRIBUTION",
                    approved,
                    collection_method="HTML",
                    expected_publish_frequency="MONTHLY",
                    acquisition_methods=("HTML", "RENDERED_BROWSER"),
                    reliability_level=3,
                    notes="Free monthly polyether polyol table by country with Incoterm. Germany CIF is collected as the European market and USA CIF as North America. Verified 2026-09-23 (July and June 2026 rows for both).",
                    collector_status="STRUCTURED_COLLECTOR",
                ),
                SourceDefinition(
                    "intratec_tdi",
                    "Intratec",
                    "Intratec TDI Prices",
                    "https://www.intratec.us/solutions/primary-commodity-prices/commodity/toluene-diisocyanate-prices",
                    "LICENSED_OR_PUBLIC_EXCERPT",
                    approved,
                    collection_method="ACQUISITION_LADDER",
                    expected_publish_frequency="MONTHLY",
                    acquisition_methods=("HTML", "DOWNLOAD", "RENDERED_BROWSER", "SCREENSHOT"),
                    requires_auth=True,
                    mapping_status="PUBLIC_MONITORING",
                    reliability_level=3,
                    notes="Public page shows only an 'illustrative historical sample' chart (Feb 2025-Feb 2026) and a stale narrative; current monthly prices need a subscription. Monitor-only (verified 2026-09-08).",
                    collector_status="MONITOR_ONLY",
                ),
            ]
            # The public trade-data layer (Stefan, 13 October 2026): Eurostat Comext per material and
            # UN Comtrade per frequency. Generated from app/trade/materials.py so a material is added
            # in one place. Imported here, not at the top, because that module imports this one.
            + _trade_layer_definitions()
        )

    @classmethod
    def expanded(cls):
        """Core independent sources plus product-specific acquisition series."""
        reg = cls.default()
        approved = RightsStatus.APPROVED
        supplemental = [
            SourceDefinition(
                "echemi_pmdi", "ECHEMI", "ECHEMI Polymeric MDI China Domestic",
                "https://www.echemi.com/productsInformation/pd20170601172413283-polymericmdi.html",
                "LICENSED_INTERNAL_USE", approved, collection_method="HTML_PRODUCT_INFORMATION",
                expected_publish_frequency="DAILY", acquisition_methods=("HTML", "RENDERED_BROWSER", "SCREENSHOT"),
                reliability_level=4, notes="Polymeric MDI China domestic price curve; use visible/subscribed data only. ECHEMI began serving its access-verification page to every request on 15 September 2026. The last reading we took is 11 September; the 228 runs since have all been turned away. The gate is not worked around (Stefan's standing rule), so the source is monitor-only until it opens or a subscription exists - registered, not fetched. Retired as a price source on 16 October 2026 (Stefan's call): across six markets it supplied 24 readings of 20,710 and was never the only publisher on any of them, while the gate has turned away every run since 15 September. The registration and its readings stay - the readings were re-read off the page's own table in migration 0030 and agree with Longzhong to within 0.9%, and two series curation had already cut are named in series_curation. ECHEMI's news lane is unaffected.",
                collector_status="MONITOR_ONLY",
                active=False,
            ),
            SourceDefinition(
                "echemi_polyol", "ECHEMI", "ECHEMI Polyether Polyol MW3000 China",
                "https://www.echemi.com/productsInformation/pd20170105140233784-polyether-polyol.html",
                "LICENSED_INTERNAL_USE", approved, collection_method="HTML_PRODUCT_INFORMATION",
                expected_publish_frequency="DAILY", acquisition_methods=("HTML", "RENDERED_BROWSER", "SCREENSHOT"),
                reliability_level=4, notes="Configured MW3000/flexible-foam polyether series; exact grade identity required. ECHEMI began serving its access-verification page to every request on 15 September 2026. The last reading we took is 11 September; the 228 runs since have all been turned away. The gate is not worked around (Stefan's standing rule), so the source is monitor-only until it opens or a subscription exists - registered, not fetched. Retired as a price source on 16 October 2026 (Stefan's call): across six markets it supplied 24 readings of 20,710 and was never the only publisher on any of them, while the gate has turned away every run since 15 September. The registration and its readings stay - the readings were re-read off the page's own table in migration 0030 and agree with Longzhong to within 0.9%, and two series curation had already cut are named in series_curation. ECHEMI's news lane is unaffected.",
                collector_status="MONITOR_ONLY",
                active=False,
            ),
            # Feedstocks. These are not products HTC sells a forecast for - they are the upstream
            # prices the correlation work needs, and the route graph says which ones are worth
            # holding. Toluene is one priced step from TDI; benzene is two from MDI.
            SourceDefinition(
                "echemi_toluene", "ECHEMI", "ECHEMI Toluene China",
                "https://www.echemi.com/productsInformation/temppid160704000607-toluene.html",
                "LICENSED_INTERNAL_USE", approved, collection_method="HTML_PRODUCT_INFORMATION",
                expected_publish_frequency="DAILY", acquisition_methods=("HTML", "RENDERED_BROWSER", "SCREENSHOT"),
                reliability_level=4,
                notes="TDI feedstock, one priced step upstream. China Domestic is governed as EXW; the page also carries a China FOB row in USD/ton. Collected for correlation analysis, not as a saleable series. ECHEMI began serving its access-verification page to every request on 15 September 2026. The last reading we took is 11 September; the 228 runs since have all been turned away. The gate is not worked around (Stefan's standing rule), so the source is monitor-only until it opens or a subscription exists - registered, not fetched. Retired as a price source on 16 October 2026 (Stefan's call): across six markets it supplied 24 readings of 20,710 and was never the only publisher on any of them, while the gate has turned away every run since 15 September. The registration and its readings stay - the readings were re-read off the page's own table in migration 0030 and agree with Longzhong to within 0.9%, and two series curation had already cut are named in series_curation. ECHEMI's news lane is unaffected.",
                collector_status="MONITOR_ONLY",
                active=False,
            ),
            SourceDefinition(
                "sunsirs_toluene", "SunSirs / 100ppi", "SunSirs China toluene spot price",
                "https://www.sunsirs.com/uk/prodetail-177.html", "PUBLIC_ATTRIBUTION", approved,
                collection_method="HTML", expected_publish_frequency="DAILY",
                acquisition_methods=("HTML", "RENDERED_BROWSER", "SCREENSHOT"), reliability_level=3,
                notes="Second toluene publisher, so the feedstock series can be corroborated the way TDI is. Not independent of 100ppi.",
                collector_status="STRUCTURED_COLLECTOR",
            ),
            SourceDefinition(
                "sunsirs_benzene", "SunSirs / 100ppi", "SunSirs China benzene spot price",
                "https://www.sunsirs.com/uk/prodetail-120.html", "PUBLIC_ATTRIBUTION", approved,
                collection_method="HTML", expected_publish_frequency="DAILY",
                acquisition_methods=("HTML", "RENDERED_BROWSER", "SCREENSHOT"), reliability_level=3,
                notes="MDI feedstock, two priced steps upstream through aniline and nitrobenzene.",
                collector_status="STRUCTURED_COLLECTOR",
            ),
            SourceDefinition(
                "sci99_pmdi", "SCI99 / Zhuochuang", "SCI99 polymeric MDI national market price",
                "https://www.sci99.com/monitor-384-0.html", "LICENSED_OR_PUBLIC_EXCERPT", approved,
                collection_method="ACQUISITION_LADDER", expected_publish_frequency="DAILY",
                acquisition_methods=("HTML", "RENDERED_BROWSER", "SCREENSHOT"),
                reliability_level=4,
                # Third publisher for China MDI. Two sources make the consolidated value a two-point
                # average, where either one moving alone moves the market price by half that move.
                # SunSirs was the obvious third and is not usable: it carries MDI at prodetail-675
                # and the last price on that page is 31 July 2018.
                notes="Public SCI99 polymeric-MDI monitor, the same endpoint shape as the TDI monitor.",
                collector_status="STRUCTURED_COLLECTOR",
            ),
            SourceDefinition(
                "longzhong_pmdi", "Longzhong / Oilchem", "Longzhong Polymeric MDI market prices",
                "https://list.oilchem.net/117/6600/", "PUBLIC_ATTRIBUTION", approved,
                collection_method="HTML_ARTICLE", expected_publish_frequency="DAILY",
                acquisition_methods=("HTML_ARTICLE", "HTML", "RENDERED_BROWSER", "SCREENSHOT"),
                reliability_level=4, notes="Visible Longzhong polymeric-MDI market articles; preserve source URL and attribution.",
                collector_status="STRUCTURED_COLLECTOR",
            ),
            SourceDefinition(
                "longzhong_polyol", "Longzhong / Oilchem", "Longzhong flexible-foam polyether market prices",
                "https://list.oilchem.net/154/6596/", "PUBLIC_ATTRIBUTION", approved,
                collection_method="HTML_ARTICLE", expected_publish_frequency="DAILY",
                acquisition_methods=("HTML_ARTICLE", "HTML", "RENDERED_BROWSER", "SCREENSHOT"),
                reliability_level=4, notes="Visible Longzhong flexible-foam polyether market articles; preserve source URL and attribution.",
                collector_status="STRUCTURED_COLLECTOR",
            ),
            SourceDefinition(
                "sci99_po", "SCI99", "SCI99 China propylene oxide daily market average",
                "https://www.sci99.com/monitor-399-0.html", "LICENSED_OR_PUBLIC_EXCERPT", approved,
                collection_method="HTML", expected_publish_frequency="DAILY",
                acquisition_methods=("HTML",), reliability_level=3,
                notes=("Propylene oxide is the whole of a flexible polyether by mass - 0.97 on route "
                       "POLYETHER, against a 0.03 glycerol initiator. Until now the only PO price held "
                       "was one producer's posting."),
                collector_status="STRUCTURED_COLLECTOR",
            ),
            SourceDefinition(
                "sunsirs_po", "SunSirs / 100ppi", "SunSirs China propylene oxide spot price",
                "https://www.sunsirs.com/uk/prodetail-438.html", "PUBLIC_ATTRIBUTION", approved,
                collection_method="HTML", expected_publish_frequency="DAILY",
                acquisition_methods=("HTML", "RENDERED_BROWSER", "SCREENSHOT"), reliability_level=3,
                notes="Second PO publisher; SunSirs is a different house from SCI99.",
                collector_status="STRUCTURED_COLLECTOR",
            ),
            SourceDefinition(
                "sci99_eo", "SCI99", "SCI99 China ethylene oxide daily market average",
                "https://www.sci99.com/monitor-937-0.html", "LICENSED_OR_PUBLIC_EXCERPT", approved,
                collection_method="HTML", expected_publish_frequency="DAILY",
                acquisition_methods=("HTML",), reliability_level=3,
                notes=("Ethylene oxide feeds polyether polyol directly on route POLYETHER, and its "
                       "glycol downstream feeds both polyester polyols."),
                collector_status="STRUCTURED_COLLECTOR",
            ),
            SourceDefinition(
                "sunsirs_eo", "SunSirs / 100ppi", "SunSirs China ethylene oxide spot price",
                "https://www.sunsirs.com/uk/prodetail-856.html", "PUBLIC_ATTRIBUTION", approved,
                collection_method="HTML", expected_publish_frequency="DAILY",
                acquisition_methods=("HTML", "RENDERED_BROWSER", "SCREENSHOT"), reliability_level=3,
                notes="Second EO publisher; SunSirs is a different house from SCI99.",
                collector_status="STRUCTURED_COLLECTOR",
            ),
            SourceDefinition(
                "sunsirs_adipic_acid", "SunSirs / 100ppi", "SunSirs China adipic acid spot price",
                "https://www.sunsirs.com/uk/prodetail-837.html", "PUBLIC_ATTRIBUTION", approved,
                collection_method="HTML", expected_publish_frequency="DAILY",
                acquisition_methods=("HTML", "RENDERED_BROWSER", "SCREENSHOT"), reliability_level=3,
                notes=("Aliphatic polyester polyol feedstock, already an edge into it on route PES-ALIPH. "
                       "Collected so the cost-floor relationship can be measured on our own daily series "
                       "rather than taken from someone's eight-row table."),
                collector_status="STRUCTURED_COLLECTOR",
            ),
            SourceDefinition(
                "sunsirs_deg", "SunSirs / 100ppi", "SunSirs China diethylene glycol spot price",
                "https://www.sunsirs.com/uk/prodetail-1332.html", "PUBLIC_ATTRIBUTION", approved,
                collection_method="HTML", expected_publish_frequency="DAILY",
                acquisition_methods=("HTML", "RENDERED_BROWSER", "SCREENSHOT"), reliability_level=3,
                notes=("The second polyester feedstock, an edge into both the aliphatic and the aromatic "
                       "polyester polyol on routes PES-ALIPH and PES-AROM."),
                collector_status="STRUCTURED_COLLECTOR",
            ),
            SourceDefinition(
                "jlc_pu_channel", "JLC (金联创)", "JLC polyurethane channel producer postings",
                "https://pu.315i.com/", "PUBLIC_ATTRIBUTION", approved,
                collection_method="HTML", expected_publish_frequency="DAILY",
                acquisition_methods=("HTML",), reliability_level=3,
                notes=("One named producer's posted price per product, free; JLC's full producer table "
                       "is behind a login. Six of the seven products are in the route graph and all six "
                       "are read - DMF is not and is skipped. Producer postings: the basis keeps them "
                       "in their own lane because a producer's cash ex-works is a different quantity "
                       "from a market assessment, not because it counts for less. In PU a posting often "
                       "leads the market, and its spread to the assessment is a signal of its own."),
                collector_status="STRUCTURED_COLLECTOR",
            ),
            SourceDefinition(
                "sci99_polyol", "SCI99", "SCI99 China flexible-foam polyether daily market average",
                "https://www.sci99.com/monitor-759-0.html", "LICENSED_OR_PUBLIC_EXCERPT", approved,
                collection_method="HTML", expected_publish_frequency="DAILY",
                acquisition_methods=("HTML",), reliability_level=3,
                notes=("Second publisher for the governed China polyether series, which ran on Longzhong "
                       "alone once ECHEMI stopped answering. 卓创 is independent of both Longzhong and "
                       "SunSirs, so it can corroborate. Read 12,587.50 CNY/t against Longzhong's 12,550 "
                       "on 18 September 2026."),
                collector_status="STRUCTURED_COLLECTOR",
            ),
            SourceDefinition(
                "100ppi_polyol_mw3000", "SunSirs / 100ppi", "Curated MN-3050D / MW3000 public quotes",
                "https://www.100ppi.com/", "PUBLIC_ATTRIBUTION", approved,
                collection_method="CURATED_IMPORT", refresh_frequency="MANUAL", expected_publish_frequency="HISTORICAL",
                acquisition_methods=("CURATED_WORKBOOK",), reliability_level=4,
                mapping_status="CURATED_IMPORT", collector_status="CURATED_IMPORT",
            ),
            SourceDefinition(
                "chemicalbook_polyol_mw3000", "ChemicalBook", "Curated MN-3050D / MW3000 public quotes",
                "https://www.chemicalbook.com/", "PUBLIC_ATTRIBUTION", approved,
                collection_method="CURATED_IMPORT", refresh_frequency="MANUAL", expected_publish_frequency="HISTORICAL",
                acquisition_methods=("CURATED_WORKBOOK",), reliability_level=4,
                mapping_status="CURATED_IMPORT", collector_status="CURATED_IMPORT",
            ),
            SourceDefinition(
                "htc_legacy_pmdi_research", "HTC Global legacy pMDI research", "Governed China pMDI research history",
                "internal://china-pmdi-research", "INTERNAL_CURATED", approved,
                collection_method="INTERNAL_IMPORT", refresh_frequency="MANUAL", expected_publish_frequency="HISTORICAL",
                acquisition_methods=("INTERNAL_CURATED",), reliability_level=3,
                mapping_status="LEGACY_IMPORT", collector_status="HISTORICAL_IMPORT",
            ),
            SourceDefinition(
                "htc_curated_polyol_mw3000", "HTC Global curated public-history workbook", "MW3000 curated history control",
                "internal://polyol-mw3000-control", "INTERNAL_CURATED", approved,
                collection_method="CURATED_IMPORT", refresh_frequency="MANUAL", expected_publish_frequency="HISTORICAL",
                acquisition_methods=("CURATED_WORKBOOK",), reliability_level=4,
                mapping_status="CURATED_IMPORT", collector_status="HISTORICAL_IMPORT",
            ),
            SourceDefinition(
                "htc_biweekly_rm_snapshot", "HTC Global historical market snapshot", "Bi-weekly PU raw-material market snapshot",
                "internal://biweekly-rm-snapshot", "INTERNAL_CURATED", approved,
                collection_method="INTERNAL_IMPORT", refresh_frequency="MANUAL", expected_publish_frequency="HISTORICAL",
                acquisition_methods=("INTERNAL_WORKBOOK",), reliability_level=4,
                mapping_status="INTERNAL_IMPORT", collector_status="HISTORICAL_IMPORT",
            ),

            # The market-intelligence sourcing lane. These carry no prices at all: they exist so the
            # engine hears about capacity, investment and ownership, which the price desks report
            # late or not at all.
            SourceDefinition(
                "echemi_news", "ECHEMI", "ECHEMI chemical news",
                "https://www.echemi.com/cms-news.html", "PUBLIC_ATTRIBUTION", approved,
                collection_method="HTML_ARTICLE_INDEX", refresh_frequency="DAILY_CHECK",
                expected_publish_frequency="DAILY", acquisition_methods=("HTML",),
                reliability_level=3, mapping_status="MARKET_INTELLIGENCE",
                notes="Headline-only event lane from the free English news index, including its Company Dynamic section. Third independent publisher lineage, which is what lets Longzhong and SunSirs be corroborated by something other than each other. No prices are read here; ECHEMI prices come from echemi_tdi. Index verified 2026-09-13. ECHEMI began serving its access-verification page to every request on 15 September 2026. The last reading we took is 11 September; the 228 runs since have all been turned away. The gate is not worked around (Stefan's standing rule), so the source is monitor-only until it opens or a subscription exists - registered, not fetched.",
                collector_status="MONITOR_ONLY",
            ),
            SourceDefinition(
                "covestro_press", "Covestro", "Covestro China newsroom",
                "https://www.covestro.com/press/zh-chs/", "PUBLIC_ATTRIBUTION", approved,
                collection_method="HTML_ARTICLE_INDEX", refresh_frequency="DAILY_CHECK",
                expected_publish_frequency="EVENT_DRIVEN", acquisition_methods=("HTML",),
                reliability_level=5, mapping_status="MARKET_INTELLIGENCE",
                notes="The producer's own voice, so publisher and actor are the same party. Low volume by nature. Index verified 2026-09-13; the June 2026 world-scale MDI train announcement is the kind of item this exists for.",
                collector_status="STRUCTURED_COLLECTOR",
            ),
            SourceDefinition(
                "longzhong_polyester", "Longzhong / Oilchem",
                "Longzhong East China polyester polyol",
                "https://www.mysteel.com/hot/1650311.html",
                "PUBLIC_ATTRIBUTION", approved,
                collection_method="HTML_NEWS_FEED", refresh_frequency="DAILY_CHECK",
                expected_publish_frequency="DAILY", acquisition_methods=("HTML",),
                reliability_level=3, mapping_status="READY",
                notes="The first live Chinese polyester polyol price the store has held. Three "
                      "adipic-acid grades - AA/BG, AA/EG, AA/BG+EG - published as daily "
                      "negotiation ranges inside a dated news feed rather than a table. Not "
                      "independent of the other Longzhong series: same publisher, same lineage. "
                      "Added 29 September 2026; the parser was built against the entry text, so "
                      "the first live run is what proves it. Published on mysteel.com, which owns "
                      "Longzhong - named here as its siblings are, so one publisher is not "
                      "registered twice.",
                collector_status="STRUCTURED_COLLECTOR",
            ),
            SourceDefinition(
                "stepan_press", "Stepan", "Stepan pricing announcements",
                "https://www.stepan.com/news-events/pricing-announcements.html",
                "PUBLIC_ATTRIBUTION", approved,
                collection_method="HTML_ARTICLE_INDEX", refresh_frequency="DAILY_CHECK",
                expected_publish_frequency="EVENT_DRIVEN", acquisition_methods=("HTML",),
                reliability_level=5, mapping_status="MARKET_INTELLIGENCE",
                notes="A producer that publishes its price letters itself, which almost none of "
                      "them do: publisher and actor are the same party, so there is nothing to "
                      "corroborate. Polyester polyols, the one family we hold almost nothing live "
                      "for. Registered 29 September 2026 with two announcements entered by hand "
                      "from a research note; the page could not be read from here, so no collector "
                      "runs against it yet.",
                collector_status="MONITOR_ONLY",
            ),
            SourceDefinition(
                "wanhua_news", "Wanhua Chemical", "Wanhua news centre",
                "https://www.whchem.com/column/100/", "PUBLIC_ATTRIBUTION", approved,
                collection_method="HTML_ARTICLE_INDEX", refresh_frequency="DAILY_CHECK",
                expected_publish_frequency="EVENT_DRIVEN", acquisition_methods=("HTML",),
                reliability_level=5, mapping_status="MARKET_INTELLIGENCE",
                notes="Wanhua's own newsroom. Verified 2026-09-13: the visible items are awards, recruitment and CSR, and the skip rules drop them. Wanhua's capacity decisions reach the market through its exchange filings, not this page.",
                collector_status="STRUCTURED_COLLECTOR",
            ),
            SourceDefinition(
                "sse_600230_filings", "Shanghai Stock Exchange", "Cangzhou Dahua (600230) filings",
                "https://www.sse.com.cn/assortment/stock/list/info/announcement/index.shtml?productId=600230",
                "PUBLIC_ATTRIBUTION", approved,
                collection_method="JSON_ENDPOINT", refresh_frequency="DAILY_CHECK",
                expected_publish_frequency="EVENT_DRIVEN", acquisition_methods=("HTML",),
                reliability_level=5, mapping_status="MARKET_INTELLIGENCE", active=False,
                notes="Registered but NOT collected. Five different parameter sets were tried against "
                      "query.sse.com.cn on 2026-09-13; every one answered cleanly with total 0 and echoed a null "
                      "SECURITY_CODE, so the endpoint never bound the issuer code under productId or securityCode "
                      "- it is the wrong endpoint, not a tuning problem. Left here for the governance trail. The "
                      "filing lane is to be rebuilt on cninfo, which covers Shenzhen-listed producers too.",
                collector_status="STRUCTURED_COLLECTOR",
            ),
            SourceDefinition(
                "sse_600309_filings", "Shanghai Stock Exchange", "Wanhua Chemical (600309) filings",
                "https://www.sse.com.cn/assortment/stock/list/info/announcement/index.shtml?productId=600309",
                "PUBLIC_ATTRIBUTION", approved,
                collection_method="JSON_ENDPOINT", refresh_frequency="DAILY_CHECK",
                expected_publish_frequency="EVENT_DRIVEN", acquisition_methods=("HTML",),
                reliability_level=5, mapping_status="MARKET_INTELLIGENCE", active=False,
                notes="Registered but NOT collected - see sse_600230_filings. To be rebuilt on cninfo.",
                collector_status="STRUCTURED_COLLECTOR",
            ),
            SourceDefinition(
                "sse_600426_filings", "Shanghai Stock Exchange", "Hualu Hengsheng (600426) filings",
                "https://www.sse.com.cn/assortment/stock/list/info/announcement/index.shtml?productId=600426",
                "PUBLIC_ATTRIBUTION", approved,
                collection_method="JSON_ENDPOINT", refresh_frequency="DAILY_CHECK",
                expected_publish_frequency="EVENT_DRIVEN", acquisition_methods=("HTML",),
                reliability_level=5, mapping_status="MARKET_INTELLIGENCE", active=False,
                notes="Registered but NOT collected - see sse_600230_filings. To be rebuilt on cninfo.",
                collector_status="STRUCTURED_COLLECTOR",
            ),
        ]
        for source in supplemental:
            reg._sources[source.source_id] = source
        return reg

    def add(self, source: SourceDefinition) -> SourceDefinition:
        """Register a source the code does not know about.

        Sources that arrive by hand are created as documents arrive, so they cannot be written into
        this file in advance. They are added here and persisted with every other source on the next
        write; `app.importing.service.imported_definitions` reads them back when the engine starts.
        """
        self._sources[source.source_id] = source
        return source

    def get(self, source_id: str) -> SourceDefinition:
        return self._sources[source_id]

    def list(self) -> list[SourceDefinition]:
        return list(self._sources.values())

    def due_sources(self) -> list[SourceDefinition]:
        """Sources the daily collector should attempt: approved, active, and able to deliver a price.

        MONITOR_ONLY publishers (login/subscription walls, no public price) stay registered for the
        governance trail but are not fetched every day - that only produced "no data" noise.
        """
        return [
            source for source in self._sources.values()
            if source.active and source.rights_status == RightsStatus.APPROVED and source.collector_status != "MONITOR_ONLY"
        ]

    def collectable_sources(self) -> list[SourceDefinition]:
        return self.due_sources()


def ensure_source_publishable(registry: SourceRegistry, source_id: str) -> SourceDefinition:
    source = registry.get(source_id)
    if not source.active or source.rights_status != RightsStatus.APPROVED:
        raise PermissionError(f"source {source_id} is not approved for production publishing")
    return source
