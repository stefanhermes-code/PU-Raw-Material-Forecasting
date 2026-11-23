from __future__ import annotations

from typing import Any

import requests


class MarketDataEngineUnavailable(RuntimeError):
    """Raised when the local Step 6 market-data service health check cannot be read."""


class MarketDataEngineSnapshot:
    def __init__(
        self,
        *,
        health: dict[str, Any],
        sources: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        identities: list[dict[str, Any]],
        references: list[dict[str, Any]],
        events: list[dict[str, Any]] | None = None,
        quality: list[dict[str, Any]] | None = None,
        quarantine: list[dict[str, Any]] | None = None,
        errors: dict[str, str] | None = None,
    ) -> None:
        self.health = health
        self.sources = sources
        self.observations = observations
        self.identities = identities
        self.references = references
        self.events = events or []
        self.quality = list(quality or [])
        # Readings that did not become markets (masterplan D). Empty on an engine older than
        # v1.2.11, which has no /quarantine endpoint - the panel then simply has nothing to show,
        # which is the same thing it shows when the queue is empty.
        self.quarantine = list(quarantine or [])
        self.errors = errors or {}


# How long a body of data may take to arrive.
#
# Measured against the live store on 30 October 2026, `/observations`:
#
#     35,515 readings, 27.9 MB, 24 fields each
#     6.0 s   the store - two queries to Supabase in Singapore
#     1.0 s   FastAPI encoding the response
#     0.2 s   the JSON itself
#    25-41 s  what it actually took, because the forecast warm-up was running
#
# The warm-up prepares all 153 markets and runs for about ten minutes after every engine start,
# holding the interpreter and the pool while it does. So the honest worst case is forty seconds,
# against a limit of thirty - and over that limit the caller got an empty list, the page threw the
# whole snapshot away and cleared its cache, and the next click paid the forty seconds again. The
# page was not slow; it was retrying a failing call.
#
# This number was last set for a store of 15,792 readings and 11.5 MB. The store has more than
# doubled since. It is generous on purpose: the page keeps its answer for fifteen minutes, so a
# long first read is paid about four times an hour, and a page that waits is better than a page
# that fails and asks again.
DATA_TIMEOUT_SECONDS = 120.0


class ImportRefused(RuntimeError):
    """The engine declined an import and said why. The sentence belongs on the screen."""


class GovernanceRefused(RuntimeError):
    """The engine refused a forecast-method governance change; the message is its reason, in words."""


# The four fields the five selector functions read, in the order the engine packs them into a
# reading. See `expanded_readings`.
_LEAN_FIELDS = ("observation_date", "publication_date", "source_id", "price")


def expanded_readings(markets) -> list[dict]:
    """The engine's per-market index, turned back into the rows the selector functions read.

    `market_lines.drawable_selections`, `priced_recently`, `priced_since` and
    `geographies_with_a_market`, and `current_market.best_row`, read exactly `identity_key`,
    `source_id`, `observation_date` (with `publication_date` as best_row's fallback) and `price` -
    and the price ONLY as "is it None", never as a value. So rows carrying those five keys and
    nothing else make those five functions behave exactly as they do on the full readings, with no
    change to any of them and none to the 26 test files that cover them.

    `price` comes back as 1 or None rather than the number. That is deliberate: a 1 cannot be drawn
    or averaged by accident, so a caller that wants a VALUE and is handed the index fails on the
    spot instead of quietly charting a flat line at 1.
    """
    rows: list[dict] = []
    for market in markets or []:
        key = market.get("identity_key")
        for reading in market.get("readings") or []:
            day, published, source, priced = (list(reading) + [None] * 4)[:4]
            rows.append({"identity_key": key, "source_id": source,
                         "observation_date": day, "publication_date": published,
                         "price": 1 if priced else None})
    return rows


# What an observation row carries about its identity rather than about the reading itself. The
# engine's compact form leaves these out; this puts them back, so every page sees the row it has
# always seen. `currency` and `source_currency` are not here: they depend on the currency that was
# ASKED for, so the engine still sends them per reading.
_FROM_THE_IDENTITY = (
    "identity_key", "product_family", "product_grade_or_chemistry", "origin", "destination",
    "market_geography", "commercial_basis", "packaging", "unit", "observation_type",
)


def _joined_to_identities(observations, identities):
    """Each reading with its identity's fields filled in. A row that already has them is untouched.

    A reading whose identity is not in the list is dropped, which is what the engine's inner join
    did before any of this: a reading with no market is not a reading a page can draw.
    """
    rows = list(observations or [])
    if not rows or "identity_key" in rows[0]:
        return rows                                  # the full form: there is nothing to join
    known = {str(row.get("identity_id") or ""): row for row in (identities or [])}
    if not known:
        # Compact readings and no identities to join them to. Handing these back would give every
        # page rows with `product_family`, `market_geography` and the rest silently missing, and a
        # page reads those for nearly everything - the selectors would simply empty. Say there are
        # no readings instead, which the snapshot already knows how to treat: it keeps the last
        # answer that had some.
        return []

    out = []
    for row in rows:
        identity = known.get(str(row.get("identity_id") or ""))
        if identity is None:
            continue
        out.append({**row, **{field: identity.get(field) for field in _FROM_THE_IDENTITY}})
    return out


class MarketDataEngineClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:7861",
        *,
        timeout: float = 2.0,
        data_timeout: float | None = None,
        session=None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        # Two timeouts, because two different questions are being asked.
        #
        # `timeout` gates availability: is the engine answering at all. It is short on purpose, so a
        # dead engine is reported in a second rather than leaving the page hanging.
        #
        # `data_timeout` is how long a body of data may take to arrive. `/observations` was 11.5 MB
        # and 1.67 seconds against a 1.5 second limit, so it timed out, the caller fell back to an
        # empty list, and Current Market drew a chart with nothing on it while every selector above
        # it worked - the selectors come from `/identities`, which is small and answered in 0.02s.
        # A slow answer is not the same failure as no answer, and reading it as one hid a working
        # engine behind an empty page.
        self.timeout = timeout
        self.data_timeout = DATA_TIMEOUT_SECONDS if data_timeout is None else data_timeout
        self.session = session or requests.Session()

    def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        allow_not_found: bool = False,
        timeout: float | None = None,
    ):
        try:
            response = self.session.get(
                f"{self.base_url}{path}",
                params=params,
                timeout=self.timeout if timeout is None else timeout,
            )
            if allow_not_found and response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            raise MarketDataEngineUnavailable(
                f"Step 6 market-data engine request failed for {path}: {exc}"
            ) from exc

    def _safe_get(
        self,
        path: str,
        *,
        default,
        errors: dict[str, str],
        params: dict[str, Any] | None = None,
        allow_not_found: bool = False,
        error_key: str | None = None,
    ):
        try:
            return self._get(path, params=params, allow_not_found=allow_not_found,
                             timeout=self.data_timeout)
        except MarketDataEngineUnavailable as exc:
            errors[error_key or path] = str(exc)
            return default

    def send_import(self, payload: dict[str, Any], *, timeout: float = 120.0) -> dict[str, Any]:
        """Hand the engine a document and the rows a person confirmed off it.

        The only write this client makes. The engine owns the store, so the page proposes and
        confirms and the engine writes; sending the file with the rows is what lets the reading and
        the evidence point at each other.

        The timeout is long on purpose. This is a person waiting for their own upload, not a page
        asking whether the engine is alive, and a document of a few megabytes takes a moment.
        """
        try:
            response = self.session.post(f"{self.base_url}/import", json=payload, timeout=timeout)
        except Exception as exc:
            raise MarketDataEngineUnavailable(f"the import could not be sent: {exc}") from exc
        if response.status_code >= 400:
            # The engine refuses an import for reasons a person can act on - a missing rights class,
            # a row without an Incoterm - so its own sentence is what reaches the screen.
            detail = ""
            try:
                detail = str(response.json().get("detail") or "")
            except Exception:
                detail = response.text[:300]
            raise ImportRefused(detail or f"the engine refused the import ({response.status_code})")
        return response.json()

    # ------------------------------------------------------------------- the investigation
    #
    # The engine owns the cases, as it owns the prices, so the page asks over HTTP rather than
    # reaching into a database - which matters more since v0.5.40, when the store moved to
    # Supabase and there is no local file to reach into.

    def send_intelligence(self, source_id: str, events: list[dict[str, Any]],
                          *, timeout: float = 60.0) -> dict[str, Any]:
        """Developments confirmed off one of HTC Global's own reports, to the engine's store.

        The register beside the application is what the Market Intelligence page reads; this is
        what the forecaster reads. Both are written, because they answer different questions and
        the page has to keep working when the engine is not up.
        """
        if not events:
            return {"stored": 0, "already_held": 0, "refused": []}
        try:
            response = self.session.post(f"{self.base_url}/intelligence/events",
                                         json={"source_id": source_id, "events": events},
                                         timeout=timeout)
        except Exception as exc:
            raise MarketDataEngineUnavailable(f"the developments could not be sent: {exc}") from exc
        if response.status_code >= 400:
            detail = ""
            try:
                detail = str(response.json().get("detail") or "")
            except Exception:
                detail = response.text[:300]
            raise ImportRefused(detail or f"the engine refused the developments ({response.status_code})")
        return response.json()

    def investigation_cases(self, *, state: str = "", gap: str = "") -> list[dict[str, Any]]:
        """Every case, with its chain of questions and its suspects."""
        params = {k: v for k, v in (("state", state), ("gap", gap)) if v}
        found = self._get("/investigation/cases", params=params or None,
                          timeout=self.data_timeout)
        return found if isinstance(found, list) else []

    def _investigation_post(self, path: str, payload: dict[str, Any] | None = None,
                            *, timeout: float = 120.0) -> dict[str, Any]:
        """A write to the investigation. Long timeout: a person is waiting for their own click.

        Opening the cases walks every event and every price series in the store, which is minutes
        rather than seconds, so this is not the data timeout.
        """
        try:
            response = self.session.post(f"{self.base_url}{path}", json=payload or {},
                                         timeout=timeout)
        except Exception as exc:
            raise MarketDataEngineUnavailable(f"{path} could not be reached: {exc}") from exc
        if response.status_code >= 400:
            detail = ""
            try:
                detail = str(response.json().get("detail") or "")
            except Exception:
                detail = response.text[:300]
            raise MarketDataEngineUnavailable(
                detail or f"the engine refused {path} ({response.status_code})")
        return response.json()

    def investigation_open(self) -> dict[str, Any]:
        """Open cases from what the engine holds and run the free enquiries. Idempotent."""
        return self._investigation_post("/investigation/open", timeout=600.0)

    def investigation_work(self, case_id: str) -> dict[str, Any]:
        return self._investigation_post(f"/investigation/cases/{case_id}/work")

    def investigation_close(self, case_id: str, *, state: str, answer: str) -> dict[str, Any]:
        return self._investigation_post(f"/investigation/cases/{case_id}/close",
                                        {"state": state, "answer": answer})

    def investigation_reopen(self, case_id: str) -> dict[str, Any]:
        return self._investigation_post(f"/investigation/cases/{case_id}/reopen")

    def investigation_rule_out(self, candidate_id: str, *, why: str) -> dict[str, Any]:
        return self._investigation_post(
            f"/investigation/candidates/{candidate_id}/rule-out", {"why": why})

    # ------------------------------------------------------------------- forecast method governance
    #
    # WP3-WP5 of the Adaptive Forecast Method Selection CR. Reads are data-timed; writes carry the
    # engine's refusal sentence to the screen (an override of a Challenger, a missing reason...).

    def model_fit(self, identity_key: str) -> dict[str, Any]:
        """Why the method in force is in force, per horizon (WP5)."""
        found = self._get("/model-fit", params={"identity_key": identity_key}, allow_not_found=True,
                          timeout=self.data_timeout)
        return found if isinstance(found, dict) else {}

    def model_fit_accuracy(self) -> dict[str, Any]:
        """How accurate the methods in force are, by chemical and by horizon, in one read.

        One call for the whole picture rather than `model_fit` per market: Forecast Performance
        shows every chemical at once, and forty round trips to draw one table is not a page.

        Empty on an engine older than v0.5.85, which has no such route, so the page falls back to
        the baselines it showed before.
        """
        found = self._get("/model-fit/accuracy", allow_not_found=True, timeout=self.data_timeout)
        return found if isinstance(found, dict) else {}

    def readings_for(self, identity_keys, *, currency: str | None = None) -> list[dict[str, Any]]:
        """The full readings of the named markets only - what a chart needs, and nothing else.

        The CALLER says which keys it wants. A chart draws a chemical in a geography, and which
        identities that covers is `market_lines`' rule (CFR China is EXW China, East China sits
        inside China); asking the engine for a chemical would put a second copy of that rule there.
        """
        keys = [str(key) for key in identity_keys if str(key or "")]
        if not keys:
            return []
        asked: dict[str, Any] = {"compact": 1, "identity_keys": ",".join(keys)}
        if currency:
            asked["currency"] = currency
        rows = self._get("/observations", params=asked, timeout=self.data_timeout) or []
        return _joined_to_identities(rows, self._get("/identities", timeout=self.data_timeout) or [])

    def sources(self) -> list[dict[str, Any]]:
        """Every source with its collection record: when it last worked, how the last try ended,
        and whether it is due. 102 rows, which is the whole point - the Dashboard needs the
        collection state and must not pull 35,517 readings to get it.
        """
        found = self._get("/sources", timeout=self.data_timeout)
        return found if isinstance(found, list) else []

    def forecasts_available(self) -> list[dict[str, Any]]:
        """Every market with a forecast method in force, the method per horizon, when it was chosen.

        `allow_not_found`: an engine older than v1.2.40 has no such endpoint, and a page that asks
        an older engine should say there is nothing to show rather than report a failure.
        """
        return self._get("/forecasts-available", allow_not_found=True,
                         timeout=self.data_timeout) or []

    def forecast_methods(self) -> list[dict[str, Any]]:
        found = self._get("/forecast-methods", timeout=self.data_timeout)
        return found if isinstance(found, list) else []

    def review_status(self) -> dict[str, Any]:
        return self._get("/forecast-methods/review-status", timeout=self.data_timeout) or {}

    def governance_settings(self) -> dict[str, Any]:
        return self._get("/forecast-methods/settings", timeout=self.data_timeout) or {}

    def overrides(self) -> list[dict[str, Any]]:
        found = self._get("/forecast-methods/overrides", timeout=self.data_timeout)
        return found if isinstance(found, list) else []

    def _governance_post(self, path: str, payload: dict[str, Any], *, timeout: float = 180.0) -> dict[str, Any]:
        """A governance write. Long timeout: setting an override asks the method once, to prove it can
        answer. A refusal raises `GovernanceRefused` with the engine's own sentence."""
        try:
            response = self.session.post(f"{self.base_url}{path}", json=payload, timeout=timeout)
        except Exception as exc:
            raise MarketDataEngineUnavailable(f"{path} could not be reached: {exc}") from exc
        if response.status_code >= 400:
            detail = ""
            try:
                detail = str(response.json().get("detail") or "")
            except Exception:
                detail = response.text[:300]
            raise GovernanceRefused(detail or f"the engine refused {path} ({response.status_code})")
        return response.json()

    def set_override(self, identity_key: str, horizon_weeks: int, method_key: str, *, actor: str,
                     reason: str) -> dict[str, Any]:
        return self._governance_post("/forecast-methods/overrides", {
            "identity_key": identity_key, "horizon_weeks": int(horizon_weeks), "method_key": method_key,
            "actor": actor, "reason": reason})

    def remove_override(self, identity_key: str, horizon_weeks: int, *, actor: str, reason: str) -> dict[str, Any]:
        return self._governance_post("/forecast-methods/overrides/remove", {
            "identity_key": identity_key, "horizon_weeks": int(horizon_weeks), "actor": actor, "reason": reason})

    def approve_challenger(self, method_key: str, *, actor: str, reason: str) -> dict[str, Any]:
        return self._governance_post("/forecast-methods/challengers/approve",
                                     {"method_key": method_key, "actor": actor, "reason": reason})

    def change_governance_setting(self, key: str, value, *, actor: str, reason: str) -> dict[str, Any]:
        return self._governance_post("/forecast-methods/settings",
                                     {"key": key, "value": value, "actor": actor, "reason": reason})

    # Both of these read the store, so both wait on `data_timeout` rather than on the availability
    # gate. They were the last two store reads left on `timeout`, which is the short one, and the
    # page that draws them builds its client with `timeout=1.5`.
    #
    # Measured against the live store on 31 October 2026, TDI China EXW: `/readiness` 1.1-2.7s and
    # `/backtest` 1.0-2.7s, the high end while the daily collection was running. So the page drew
    # "The Step 6 market-data engine is not reachable. Start the application with Run-MVP.bat." at
    # a reader, in red, with the engine running and answering - a false statement on the one screen
    # whose whole subject is whether the application tells the truth about itself.
    #
    # It is the defect this class already names, one layer up: *"A slow answer is not the same
    # failure as no answer, and reading it as one hid a working engine behind an empty page."*
    # Eighteen store reads here, fifteen of them already moved to `data_timeout`; these two and
    # `events` below were the ones nobody ruled on.
    #
    # A dead engine is not made slower by this. Measured the same day against a port with nothing
    # listening: refused in 1.52s at a 1.5s limit and 2.02s at a 15s one, because Windows gives up
    # on the connection itself rather than waiting out the limit.
    def readiness(self, identity_key: str) -> dict[str, Any]:
        return self._get("/readiness", params={"identity_key": identity_key},
                         timeout=self.data_timeout)

    def backtest(self, identity_key: str) -> dict[str, Any]:
        return self._get("/backtest", params={"identity_key": identity_key},
                         timeout=self.data_timeout)

    def governed_reference_series(self, identity_key: str) -> dict[str, Any]:
        """One market's governed price series: a value per date, net of tax, with its spread.

        Named apart from `reference_series` below, which is a different thing entirely - non-price
        indices and proxies, no identity, no tax. This was called `reference_series` for about an
        hour on 17 September 2026, defined earlier in the same class, and Python quietly kept the
        second definition. The page then called it with an identity key it would not accept and the
        whole screen fell over. Two methods cannot share a name; a test below now says so.

        Empty on an engine older than v0.4.170, which has no such route. The page then says the
        history is being collected rather than falling back to the raw prints - those carry China's
        13% VAT and showing them beside a forecast that does not is the fault this exists to fix.
        """
        errors: dict[str, str] = {}
        payload = self._safe_get("/reference/governed-series",
                                 params={"identity_key": identity_key},
                                 allow_not_found=True, default=None, errors=errors)
        return payload or {"identity_key": identity_key, "points": []}

    def governed_forecast(self, identity_key: str,
                          horizons: tuple[int, ...] = (4, 8, 12)) -> dict[str, Any]:
        """The forecast of the method IN FORCE for this market, per horizon, as it was stored.

        Named apart from `forecast` below, which asks the engine to compute every eligible model on
        the spot. This asks for one number per horizon: the one the governed selection chose, run by
        the production job after data collection and written down.

        That split is acting-architect decision D5 (20 October 2026) and it is not an accident of
        layering. The selected methods are Chronos-2, Chronos-Bolt and TimesFM for most markets, and
        those load neural weights; computing them while somebody waits on a page would make the
        screen unusable. So they are computed once by a job and the page reads the result.

        Each horizon comes back with its own `status`:

            SUCCEEDED     a stored forecast, under "forecast": value, period, quantiles
            PENDING       the method in force has not been run yet
            FAILED        it ran and refused, with the reason
            NOT_GOVERNED  no override and no active selection for this pair

        and, when SUCCEEDED, `current` with `stale_reasons` - how old the answer is and how old the
        data under it is. A governed number is not presented as today's without that check.

        Empty on an engine older than v0.5.70, which has no such route, so the page falls back to
        what it has always shown rather than breaking.
        """
        errors: dict[str, str] = {}
        payload = self._safe_get("/governed-forecast", default=None, errors=errors,
                                 allow_not_found=True,
                                 params={"identity_key": identity_key,
                                         "horizons": ",".join(str(h) for h in horizons)})
        if not isinstance(payload, dict):
            return {"identity_key": identity_key, "horizons": {}}
        return payload

    def forecast(self, identity_key: str, horizons: tuple[int, ...] = (4, 8, 12)) -> dict[str, Any]:
        """Every eligible model's answer for one identity, at calendar-week horizons.

        Empty on an engine older than v0.4.101, so a stale engine degrades to the cards it has always
        shown rather than breaking the main screen.
        """
        errors: dict[str, str] = {}
        payload = self._safe_get("/forecast", default={}, errors=errors, allow_not_found=True,
                                 params={"identity_key": identity_key,
                                         "horizons": ",".join(str(h) for h in horizons)})
        return payload if isinstance(payload, dict) else {}

    def forecast_readiness(self) -> dict[str, Any]:
        """How far the engine has got preparing every market's forecast. Empty on an older engine."""
        errors: dict[str, str] = {}
        payload = self._safe_get("/forecast-readiness", default={}, errors=errors,
                                 allow_not_found=True)
        return payload if isinstance(payload, dict) else {}

    def forecast_vintages(self, identity_key: str, *, limit: int = 500) -> list[dict[str, Any]]:
        """Every forecast this market was given, as it was given, newest first.

        The record a method is judged on. Empty on an engine with no vintage store, so the
        performance screen degrades to the backtest it has always shown.
        """
        errors: dict[str, str] = {}
        payload = self._safe_get("/forecast-vintages", default=[], errors=errors,
                                 allow_not_found=True,
                                 params={"identity_key": identity_key, "limit": limit})
        return payload if isinstance(payload, list) else []

    def forecast_performance(self, identity_key: str = "") -> list[dict[str, Any]]:
        """What the recorded claims turned out to be worth, per model and horizon."""
        errors: dict[str, str] = {}
        payload = self._safe_get("/forecast-performance", default=[], errors=errors,
                                 allow_not_found=True,
                                 params={"identity_key": identity_key} if identity_key else None)
        return payload if isinstance(payload, list) else []

    def events(self) -> list[dict[str, Any]]:
        """Market-intelligence events (producer price announcements, plant events, fundamentals).

        A store read, so `data_timeout`: 428 KB and 0.41-0.47s measured on 31 October 2026, which
        fits inside the 1.5s availability gate today and would not have on a slower answer.
        """
        return self._get("/events", timeout=self.data_timeout) or []

    def fx_rates(self, currencies: tuple[str, ...] | None = None) -> dict[str, Any]:
        """Every stored ECB fixing. A body of data, so it gets the data timeout.

        It was asked for on the availability timeout, which is short on purpose. 16,295 fixings
        requested on the first render while the engine warms its forecasts overran it, the caller
        fell back to an empty table and kept it, and every yuan reading then drew on a dollar axis
        (Stefan, 27 September 2026). Slow is not the same as absent.
        """
        params = {"currencies": ",".join(currencies)} if currencies else None
        return self._get("/fx/rates", params=params, timeout=self.data_timeout)

    def imports(self) -> dict[str, Any]:
        """What the engine's startup imports did. Empty on an engine older than v0.4.82."""
        errors: dict[str, str] = {}
        payload = self._safe_get("/imports", default={}, errors=errors, allow_not_found=True)
        return payload if isinstance(payload, dict) else {}

    def coverage(self, days: int = 45) -> dict[str, Any]:
        """Which trading days each daily series is missing. Empty on an engine older than v0.4.87."""
        errors: dict[str, str] = {}
        payload = self._safe_get("/coverage", default={}, errors=errors, params={"days": days},
                                 allow_not_found=True)
        return payload if isinstance(payload, dict) else {}

    def reference_series(self, *, internal: bool = False) -> list[dict[str, Any]]:
        """Non-price history: indices, proxies and benchmarks from other markets.

        `internal` asks the engine to include the series HTC holds under terms that do not allow
        showing them on. It is passed only by the internal view; a customer session never sets it,
        and an engine that predates the gate ignores the parameter and returns the public set.

        Returned empty rather than raising when the engine predates v0.4.81, so a stale engine
        degrades to "no reference data" instead of breaking the page.
        """
        errors: dict[str, str] = {}
        payload = self._safe_get("/reference-series", default=[], errors=errors,
                                 allow_not_found=True,
                                 params={"internal": "true"} if internal else None)
        return payload if isinstance(payload, list) else []

    def transactions(self, *, chemical: str = "", geography: str = "", origin: str = "",
                     internal: bool = False) -> list[dict[str, Any]]:
        """HTC's own purchase history as its own series, for drawing beside a published assessment.

        `internal` must be set or the engine returns nothing. The whole lane is confidential - what
        HTC paid each supplier, and the spread between them - so there is no public subset to fall
        back to, and a caller that has not thought about it gets an empty list rather than a leak.

        Empty on an engine older than v0.4.132, so a stale engine simply draws one line.
        """
        errors: dict[str, str] = {}
        params = {k: v for k, v in (("chemical", chemical), ("geography", geography),
                                    ("origin", origin)) if v}
        if internal:
            params["internal"] = "true"
        payload = self._safe_get("/transactions", default=[], errors=errors,
                                 allow_not_found=True, params=params or None)
        return payload if isinstance(payload, list) else []

    def snapshot(self, *, reference_cutoff: str | None = None,
                 currency: str | None = None,
                 readings: str = "full") -> MarketDataEngineSnapshot:
        """Everything the page needs, with the readings already in `currency`.

        The conversion happens when a datapoint is created, not here and not on the page (Stefan,
        27 September 2026). Asking the engine for a currency it holds no value in returns a null
        price on that reading rather than the number it is assessed in, so nothing downstream can
        draw a yuan price on a dollar axis.
        """
        # Health is the only hard availability gate. Once health answers, failures
        # on individual read-only data endpoints are reported separately so the
        # UI can distinguish "engine down" from "one endpoint failed".
        health = self._get("/health")
        errors: dict[str, str] = {}

        sources = self._safe_get("/sources", default=[], errors=errors)
        events = self._safe_get("/events", default=[], errors=errors)
        # The readings WITHOUT the identity fields, which are then joined back on from
        # `/identities` below. Measured on the live store, 30 October 2026: the full form is 27.9 MB
        # over 35,515 readings, of which 7.03 MB is `evidence_id` - a 64-character hash no page
        # reads off an observation - plus eleven identity fields repeated on every row although
        # there are only 339 identities and they arrive in their own call anyway.
        #
        # An engine that predates `compact` does not fail on it - FastAPI ignores a query parameter
        # it does not declare - so it simply answers the full form, and `_joined_to_identities`
        # finds the fields already there and hands the rows back untouched. `allow_not_found` and
        # the retry below are for the other case, an engine with no `/observations` at all. Either
        # way the rows a page receives are identical to what it received before.
        # `readings="index"` is what a page filling dropdowns needs: four fields per reading, no
        # prices, no currency conversion. 1.5 MB against 15.2 (measured, 30 October 2026). A page
        # that draws numbers asks for "full" and gets what it always got.
        if readings == "index":
            packed = self._safe_get("/observations", default=[], errors=errors,
                                    params={"lean": 1})
            observations = expanded_readings(packed)
            identities = self._safe_get("/identities", default=[], errors=errors)
        else:
            asked = {"compact": 1}
            if currency:
                asked["currency"] = currency
            observations = self._safe_get("/observations", default=None, errors=errors,
                                          params=asked, allow_not_found=True)
            identities = self._safe_get("/identities", default=[], errors=errors)
            if observations is None:
                observations = self._safe_get(
                    "/observations", default=[], errors=errors,
                    params={"currency": currency} if currency else None)
            observations = _joined_to_identities(observations, identities)
        # Data-quality issues, so a frozen feed can be shown beside the source that froze.
        # allow_not_found: an engine older than v0.4.191 has no /quality endpoint.
        quality = self._safe_get("/quality", default=[], errors=errors, allow_not_found=True)
        # allow_not_found for the same reason /quality has it: an engine older than v1.2.11 has no
        # quarantine, and a missing endpoint is not a failure to report on the page.
        quarantine = self._safe_get("/quarantine", default=[], errors=errors,
                                    allow_not_found=True)

        references: list[dict[str, Any]] = []
        # One call for every identity's newest reference, where the engine offers it.
        #
        # This loop used to make one HTTP request per identity - 70 of them, then 113 after the ICIS
        # import, each one loading the engine's whole identity table to find a single row. The page
        # stopped finishing. `/references` answers all of them in one query. An engine older than
        # v0.4.253 has no such endpoint, so the per-identity loop stays as the fallback and the
        # cutoff case, which asks a different question, still goes round one at a time.
        if reference_cutoff is None:
            bulk = self._safe_get("/references", default=None, errors=errors,
                                  allow_not_found=True)
            # The per-identity loop below is for an engine that has no `/references` at all (a 404).
            # An engine that has it but answered too slowly - busy preparing forecasts - must not be
            # asked 329 times instead: on 13 October 2026 that is what held the page at "almost
            # ready" for over ten minutes. The snapshot goes out without references; the pages
            # already cope with a market that has none.
            if bulk is None and "/references" in errors:
                bulk = []
            if bulk is not None:
                by_key = {str(row.get("identity_key")): row for row in bulk}
                for identity in identities:
                    reference = by_key.get(str(identity.get("identity_key")))
                    if reference is not None:
                        references.append({**identity, **reference})
                return MarketDataEngineSnapshot(
                    health=health, sources=sources, observations=observations,
                    identities=identities, references=references, events=events,
                    quality=quality, quarantine=quarantine, errors=errors,
                )

        for identity in identities:
            identity_key = identity["identity_key"]
            reference = None
            if reference_cutoff is not None:
                reference = self._safe_get(
                    "/reference/as-of",
                    params={"identity_key": identity_key, "cutoff": reference_cutoff},
                    allow_not_found=True,
                    default=None,
                    errors=errors,
                    error_key=f"/reference/as-of?identity_key={identity_key}",
                )
            if reference is None:
                reference = self._safe_get(
                    "/reference",
                    params={"identity_key": identity_key},
                    allow_not_found=True,
                    default=None,
                    errors=errors,
                    error_key=f"/reference?identity_key={identity_key}",
                )
            if reference is not None:
                references.append({**identity, **reference})

        return MarketDataEngineSnapshot(
            health=health,
            sources=sources,
            observations=observations,
            identities=identities,
            references=references,
            events=events,
            quality=quality if isinstance(quality, list) else [],
            quarantine=quarantine if isinstance(quarantine, list) else [],
            errors=errors,
        )
