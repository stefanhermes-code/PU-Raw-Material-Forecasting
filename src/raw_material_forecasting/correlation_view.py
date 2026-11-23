"""Screen-ready view of the price-relationship study.

`correlation.py` does the statistics; this turns them into what a reader needs to see, in the order
they need it: what we hold, what is missing, and only then any relationship we can actually claim.

The order is deliberate. A correlation table at the top of a page invites the reader to take a number
from it, and today almost every pair is untestable for want of an upstream series. Leading with the
gap is the honest presentation, and it is also the useful one - the gap is the thing HTC can act on.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from .chemical_routes import ChemicalRouteGraph
from .announcement_timing import (announcement_timing, summarise as summarise_announcements,
                                  timing_frame)
from .correlation import (
    MIN_WEEKLY_DENSITY,
    MIN_SERIES_WEEKS,
    chain_study,
    lead_diagnostic,
    lead_rows,
    weekly_readiness,
    lag_consistency,
    link_rows,
    CONFIDENT_MONTHS,
    PairResult,
    MIN_OVERLAP_MONTHS,
    MonthlySeries,
    PairResult,
    Requirement,
    build_series,
    data_requirements,
    _basis,
    reference_series_to_monthly,
    requirement_rows,
    result_rows,
    study,
)
from .fx import FxTable
from .units import display_unit
from .route_diagram import route_chemicals_table, route_dot, route_summary, route_table
from .platform_admin.access import entitled_chemicals

STATUS_LABELS = {
    "ready": "Ready to test",
    "short_history": "Too short",
    "too_short": "Too short to measure",
    "no_overlap": "No overlap",
    "missing": "No data",
}


@dataclass(frozen=True)
class RelationshipView:
    target: str
    target_label: str
    chemical_labels: dict[str, str]
    series: list[MonthlySeries]
    target_series: list[MonthlySeries]
    requirements: list[Requirement]
    results: list[PairResult]
    min_overlap: int = MIN_OVERLAP_MONTHS
    links: list = field(default_factory=list)
    graph: Any = None
    # Which market's price is being explained. Empty means every market at once, which is what this
    # view did before it could be told, and what made a Chinese reader read a European answer.
    target_geography: str = ""
    # What the trade calls it, for use inside a sentence. `target_label` carries the full chemical
    # name after the abbreviation, which belongs in a heading and not seven times down a column.
    target_short: str = ""

    @property
    def short_name(self) -> str:
        return self.target_short or self.target_label

    @property
    def held(self) -> int:
        return sum(1 for r in self.requirements if r.status != "missing")

    @property
    def upstream_total(self) -> int:
        return len(self.requirements)

    @property
    def testable(self) -> int:
        """Chemicals that can be correlated at all today, firm or not."""
        return sum(1 for r in self.requirements if r.status in {"ready", "testable"})

    @property
    def firm(self) -> int:
        return sum(1 for r in self.requirements if r.status == "ready")

    @property
    def reportable(self) -> list[PairResult]:
        return [r for r in self.results if r.reportable]


def available_targets(graph: ChemicalRouteGraph, allowed: frozenset[str] | None = None) -> list[str]:
    """Raw materials this viewer may look at: what the seed declares, narrowed by the entitlement.

    A customer who bought TDI has no business seeing that HTC tracks a polyester polyol chain at all,
    so the selector is filtered here rather than the tables being blanked further down.
    """
    codes = list(graph.raw_materials)
    return codes if allowed is None else entitled_chemicals(codes, allowed)


def display_name(graph: ChemicalRouteGraph, code: str) -> str:
    """The name the seed gives a chemical.

    Never title-cased. Chemical names are not prose - TDI is TDI, not Tdi, and pMDI, o-Xylene and
    1,4-BDO all lose their meaning under a general-purpose capitalisation rule. A code with no node
    is shown as written, minus the underscores.
    """
    node = graph.node(code)
    return node.label if node else code.replace("_", " ")


def build_view(observations: Sequence[dict[str, Any]], fx: FxTable, graph: ChemicalRouteGraph,
               *, target: str = "TDI", max_priced_steps: int = 3,
               reference: Sequence[dict[str, Any]] | None = None,
               geography: str | None = None) -> RelationshipView:
    # Prices and reference series sit side by side from here on: both are monthly series of numbers
    # that move, which is all a correlation needs. They were kept apart in the store so that an index
    # could never be read as money; nothing downstream of this point quotes either as a price.
    every_series = build_series(observations, fx, graph) + reference_series_to_monthly(reference or ())
    # Only this chain: the raw material itself and the chemicals upstream of it. A series for another
    # raw material is not evidence about this one and must not be shown beside it.
    chain = {target} | {
        c.chemical for c in graph.upstream_candidates(target, max_priced_steps=max_priced_steps)
    }
    series = [row for row in every_series if row.chemical in chain]
    # The raw material is priced in one market at a time; its feedstocks are not.
    #
    # Nothing here knew about geography, so a China TDI price and a Western Europe TDI price sat in
    # one pile and the pairing with the most months won - which is Western Europe every time, on the
    # 151-month report series. A Chinese reader was shown a European answer with nothing on the page
    # saying so. Whether propylene moves polyether polyol has a different answer in each market: 0.53
    # over 150 months in Western Europe, 0.16 over 39 in China.
    #
    # Only the raw material is narrowed. Brent is one price for the world, a US chlor-alkali index
    # stands in for chlorine wherever chlorine is made, and a feedstock quoted in Rotterdam still
    # moves a Chinese chain. Narrowing the drivers to the market would throw away most of what is
    # held and answer a smaller question than the one being asked.
    if geography:
        series = [row for row in series
                  if row.chemical != target or row.market_geography == geography]
    return RelationshipView(
        target=target,
        target_label=display_name(graph, target),
        target_geography=geography or "",
        target_short=(graph.node(target).display_name if graph.node(target) else target),
        chemical_labels={code: display_name(graph, code) for code in ({target} | chain)},
        series=series,
        target_series=[row for row in series if row.chemical == target],
        requirements=data_requirements(target, series, graph, max_priced_steps=max_priced_steps),
        results=study(target, series, graph, max_priced_steps=max_priced_steps),
        links=chain_study(target, series, graph, max_priced_steps=max_priced_steps),
        graph=graph,
    )


def _kind(series) -> str:
    if series.is_reference:
        return {"STRONG": "Benchmark price", "USABLE_PROXY": "Index (proxy)",
                "WEAK_PROXY": "Index (weak proxy)"}.get(series.quality, "Index")
    # "Transaction price", never "HTC purchase". HTC buys nothing. These rows are FNAG's and
    # FoamPartner's own transaction records, and calling them a purchase by HTC put a claim on the
    # screen that is not true of a single row in the store.
    return "Transaction price" if series.observation_type == "transaction_price" else "Published price"


def _series_rows(series) -> list[dict[str, Any]]:
    return [
        {
            "Series": s.label,
            "Kind": _kind(s),
            "Unit or index base": display_unit(s.unit) if s.is_reference else s.currency,
            "Months": s.months,
            "From": s.first.isoformat() if s.first else "-",
            "To": s.last.isoformat() if s.last else "-",
        }
        for s in sorted(series, key=lambda s: (s.first or s.last, s.label))
    ]


def target_series_rows(view: RelationshipView) -> list[dict[str, Any]]:
    """The raw material's own history, on its own.

    It used to sit in one list with the feedstocks, sorted alphabetically, which put five TDI rows
    between chlorine and toluene and invited the reader to compare a 152-month index with a 28-month
    price as though the larger number were the better one. They answer different questions: this
    table is what every relationship is measured *against*, and its shortness is the finding.
    """
    return _series_rows(view.target_series)


def upstream_series_rows(view: RelationshipView) -> list[dict[str, Any]]:
    """One row per upstream series. Kept behind the per-chemical table, not in front of it.

    `Kind` matters more than it looks: a transaction price is what one party paid under contract and
    lags the market, so a relationship fitted on it describes those contracts rather than the market.
    """
    held = {s.identity_key for s in view.target_series}
    upstream = sorted((s for s in view.series if s.identity_key not in held),
                      key=lambda s: (s.chemical, s.first or s.last))
    return [
        {
            "Chemical": view.chemical_labels.get(s.chemical, s.chemical.replace("_", " ")),
            "Series": s.label,
            "Kind": _kind(s),
            "Unit or index base": display_unit(s.unit) if s.is_reference else s.currency,
            "Months": s.months,
            "From": s.first.isoformat() if s.first else "-",
            "To": s.last.isoformat() if s.last else "-",
        }
        for s in upstream
    ]


def coverage_rows(view: RelationshipView) -> list[dict[str, Any]]:
    """Kept for callers that still name it: every series, target and upstream together."""
    return [
        {
            "Chemical": view.chemical_labels.get(s.chemical, s.chemical.replace("_", " ")),
            "Series": s.label,
            "Kind": _kind(s),
            "Basis": display_unit(s.unit) if s.is_reference else s.currency,
            "Months": s.months,
            "From": s.first.isoformat() if s.first else "-",
            "To": s.last.isoformat() if s.last else "-",
        }
        for s in sorted(view.series, key=lambda s: (s.chemical, s.first or s.last))
    ]


def gap_rows(view: RelationshipView) -> list[dict[str, Any]]:
    return requirement_rows(view.requirements)


def measurable(view: RelationshipView) -> list[PairResult]:
    """Pairs that share any history at all.

    Every driver is tested against every series of the raw material, and the 2014-2017 transaction
    records share no months with a 2024-2025 upstream pack. Those pairs are real and complete in
    `view.results`, but as table rows they are two dozen lines of "Not measurable" hiding the six that
    say something. What is missing belongs in the missing-data table, not in the results.
    """
    return [row for row in view.results if row.overlap_months > 0]


def relationship_rows(view: RelationshipView) -> list[dict[str, Any]]:
    return result_rows(measurable(view))


def best_per_chemical(view: RelationshipView) -> list[PairResult]:
    """One result per chemical: the pair with the most overlapping months.

    Every driver series is tested against every series of the raw material, so seven measurable
    chemicals produced sixty-eight rows - Brent three times over, at three different coefficients,
    against three different polyol series. A reader cannot take a number from that, and it puts a
    third population on a page whose whole point is that it counts one.

    Most overlapping months is the rule, rather than the largest coefficient, because picking the
    strongest of several attempts on the same chemical is how a number gets quoted that nothing
    supports. The pair with the most evidence behind it wins, whatever it says.
    """
    best: dict[str, PairResult] = {}
    for result in measurable(view):
        # A pairing with an overlap too short to produce a number belongs in the holdings table,
        # under "Very short", not in a results table as a row of dashes. Counting those rows here
        # is also what made the sentence above the table disagree with the table.
        if not result.reportable:
            continue
        code = result.driver.chemical
        held = best.get(code)
        if held is None or result.overlap_months > held.overlap_months:
            best[code] = result
    return sorted(best.values(), key=lambda r: (r.priced_steps, -r.overlap_months,
                                                r.driver.chemical))


# How far behind the freshest market a market may be and still be offered first. One month: on
# monthly data a market that has not printed since the month before last is not the one to open on
# while another printed this month, however much history it carries. Western Europe holds 151 months
# and last printed in July; China holds 80 and printed in September, and China is the live market.
CURRENT_WITHIN_MONTHS = 1


def target_markets(view: RelationshipView) -> list[str]:
    """The markets the raw material itself is priced in, best-evidenced first.

    Built from an unfiltered view, so the page can offer the choice before making it. Ranked the way
    the Current Market selectors rank: anything still being published first, then the most history.
    """
    months: dict[str, int] = {}
    latest: dict[str, Any] = {}
    for row in view.target_series:
        market = row.market_geography or "Not specified"
        months[market] = months.get(market, 0) + row.months
        if row.last and (market not in latest or row.last > latest[market]):
            latest[market] = row.last
    if not months:
        return []
    freshest = max(latest.values(), default=None)

    def current(market: str) -> int:
        day = latest.get(market)
        if not day or not freshest:
            return 0
        behind = (freshest.year - day.year) * 12 + (freshest.month - day.month)
        return 1 if behind <= CURRENT_WITHIN_MONTHS else 0

    return sorted(months, key=lambda m: (-current(m), -months[m], m))


def market_labels(view: RelationshipView) -> dict[str, str]:
    """Each market, with the month it last printed when it has stopped printing.

    Europe holds thirty-six months of history that ends in January 2017 and produces the best-looking
    figures on the page. Offering it beside China under a bare name invites a reader to act on a
    relationship measured before either of us was collecting.
    """
    latest: dict[str, Any] = {}
    for row in view.target_series:
        market = row.market_geography or "Not specified"
        if row.last and (market not in latest or row.last > latest[market]):
            latest[market] = row.last
    freshest = max(latest.values(), default=None)
    labels: dict[str, str] = {}
    for market, day in latest.items():
        behind = ((freshest.year - day.year) * 12 + (freshest.month - day.month)) if freshest else 0
        labels[market] = (market if behind <= CURRENT_WITHIN_MONTHS
                          else f"{market} — last priced {day:%b %Y}")
    return labels


def _strip_publisher_code(label: str) -> str:
    """Drop a leading series code. `WPU06130302 - Chlorine, Sodium Hydroxide...` is a FRED
    identifier, and on a customer table it is eleven characters of noise in front of the thing the
    reader needs. Every pairing keeps its full label on Platform Admin, where the code is the point.
    """
    return re.sub(r"^[A-Z][A-Z0-9]{2,}\s+-\s+", "", str(label or "")).strip()


def _finding(coefficient: float, interval, target_label: str) -> tuple[str, bool]:
    """What the number says, in a sentence, and whether a lead time is worth printing beside it."""
    if interval and interval[0] <= 0 <= interval[1]:
        # The range spans zero, so the measurement does not even fix the sign. Printing 0.15 beside
        # a lead time invites a reader to take a direction out of a number that has none.
        return "Cannot tell - the measurement allows both directions", False
    magnitude = abs(coefficient)
    direction = "with" if coefficient > 0 else "against"
    if magnitude >= 0.6:
        return f"Moves strongly {direction} {target_label}", True
    if magnitude >= 0.4:
        return f"Moves {direction} {target_label}", True
    if magnitude >= 0.25:
        return f"Moves weakly {direction} {target_label}", True
    return f"No movement in step with {target_label}", False


def measured_rows(view: RelationshipView) -> list[dict[str, Any]]:
    """The customer table: one row per chemical, read left to right as a sentence.

    It used to be eleven columns - two of which held the same string on every row - with the finding
    assembled by the reader out of a coefficient, an interval, a confidence grade and a verdict that
    could say "Established" and "No relationship visible" side by side. Those are two answers to two
    questions, and putting them in adjacent columns with nothing to say so read as a contradiction.

    Here the finding is a sentence, and how far to trust it is the months and the source it was
    measured on, which a reader can check.
    """
    rows: list[dict[str, Any]] = []
    for result in best_per_chemical(view):
        best = result.best
        interval = best.interval
        finding, show_lead = _finding(best.coefficient, interval, view.short_name)
        strength = (f"{best.coefficient:.2f}, between {interval[0]:.2f} and {interval[1]:.2f}"
                    if interval else f"{best.coefficient:.2f}")
        evidence = f"{result.overlap_months} months · {_strip_publisher_code(result.driver.label)}"
        quality = _basis(result.driver)
        if quality and quality != "Benchmark price":
            evidence += f" ({quality.lower()})"
        rows.append({
            "Chemical": view.chemical_labels.get(result.driver.chemical,
                                                 result.driver.chemical.replace("_", " ")),
            "What we found": finding,
            "Months ahead": str(best.lag_months) if show_lead else "—",   # text: shares a column with the dash
            "How strong": strength,
            "Measured with": evidence,
        })
    # Strongest first, and anything the measurement cannot resolve at the bottom where it belongs.
    rows.sort(key=lambda row: (row["What we found"].startswith("Cannot tell"),
                               -abs(float(row["How strong"].split(",")[0]))))
    return rows


def headline_rows(view: RelationshipView) -> list[dict[str, Any]]:
    """Kept for callers that still name it."""
    return measured_rows(view)


def headline(view: RelationshipView) -> str:
    """One plain sentence about where this raw material stands. No jargon: the reader is a buyer."""
    if not view.target_series:
        return (
            f"No price history is held for {view.target_label} itself, so nothing can be measured "
            "against it."
        )
    if view.upstream_total == 0:
        return f"No production route is recorded for {view.target_label}."
    # Counted on the table below it, never on a different population. The sentence used to count
    # requirements that had passed a months threshold while the table counted pairings that had
    # produced a number, and on Polyether polyol those were 4 and 5.
    # Counted on the table below it, never on a different population. The sentence used to count
    # requirements that had passed a months threshold while the table counted pairings that had
    # produced a number, and on Polyether polyol those were 4 and 5.
    results = best_per_chemical(view)
    measured = len(results)
    if measured == 0:
        return (
            f"None of the {view.upstream_total} chemicals that make {view.target_label} has enough "
            "price history yet to measure against it."
        )
    # Lead with what was found. A count of what could be measured reads as good news above a table
    # where nothing moves, and the finding was sitting in row three.
    #
    # Only rows whose range holds one sign are eligible. Ranking on the bare figure said "hydrogen
    # moves weakly with Polyether polyol in China: 0.32" above a table whose first row said the
    # measurement could not fix the direction - the sentence and the table disagreeing again, which
    # is the whole fault this page was rebuilt to end.
    where = f" in {view.target_geography}" if view.target_geography else ""
    decided = [r for r in results
               if not (r.best.interval and r.best.interval[0] <= 0 <= r.best.interval[1])]
    if not decided:
        return (
            f"Nothing we hold settles which way {view.short_name}{where} moves. Every figure "
            f"below spans zero, which means the months behind it do not fix even the direction. "
            f"{measured} of the {view.upstream_total} chemicals that make it are measurable at all."
        )
    strongest = max(decided, key=lambda r: abs(r.best.coefficient))
    name = view.chemical_labels.get(strongest.driver.chemical,
                                    strongest.driver.chemical.replace("_", " "))
    value = abs(strongest.best.coefficient)
    if value < 0.25:
        return (
            f"Nothing we hold moves {view.short_name}{where} in step with it. The closest is "
            f"{name} at {value:.2f} over {strongest.overlap_months} months, which is too little to "
            "act on."
        )
    if value < 0.4:
        return (
            f"{name} moves weakly with {view.short_name}{where}: {value:.2f} over "
            f"{strongest.overlap_months} months. Nothing we hold moves it more than that."
        )
    return (
        f"{name} moves with {view.short_name}{where}: {value:.2f} over "
        f"{strongest.overlap_months} months, {strongest.best.lag_months} month(s) ahead. "
        f"{measured} of the {view.upstream_total} chemicals that make it can be measured at all."
    )



# ----------------------------------------------------------------------------- Streamlit rendering

MAX_STEPS_BACK = 8
# The customer page measures the whole chain and never asks the reader how far to reach. At this
# setting the set of chemicals the study measures is exactly the set the diagram draws as quoted,
# for every raw material in the seed - which is what lets one number stand for the whole page.
FULL_CHAIN = MAX_STEPS_BACK


def import_warnings(status: dict[str, Any] | None) -> list[str]:
    """Plain sentences about anything the engine failed to load at startup.

    Without these, a data file the engine could not find reads on screen exactly like a chemical
    nobody has sourced yet - which is how a path bug survived four releases.
    """
    if not status:
        return []
    messages: list[str] = []
    transactions = status.get("transactions") or {}
    if transactions.get("status") == "missing_file":
        messages.append(
            f"The transaction price history was not loaded: the file {transactions.get('path')} was "
            "not found. The FoamPartner and KGI series will be absent until it is."
        )
    elif transactions.get("status") == "failed":
        messages.append(f"The transaction price history failed to load: {transactions.get('error')}")

    packs = status.get("reference_packs") or {}
    if packs.get("status") == "no_packs":
        messages.append(
            f"No upstream reference packs were found in {packs.get('directory')}, so no index or "
            "proxy series are available."
        )
    elif packs.get("status") == "failed":
        messages.append(f"Upstream reference packs failed to load: {packs.get('error')}")
    else:
        for pack in packs.get("packs") or ():
            if pack.get("status") == "failed":
                messages.append(f"Reference pack {pack.get('pack')} failed to load: {pack.get('error')}")
    return messages


def targets_with_a_study(observations: Sequence[dict[str, Any]], fx: FxTable,
                         graph: ChemicalRouteGraph, *, allowed: frozenset[str] | None = None,
                         max_priced_steps: int | None = None) -> list[str]:
    """The raw materials this viewer can actually be shown a study for.

    Stefan, 11 October 2026: "I am still sick of seeing choices in drop down with no results."
    Six of the twelve raw materials the graph declares produce no market at all - the chain is
    there, the prices under it are not - and every one of them was in the selector.

    The test is the page's own: build the view and see whether it yields a market. Half a second
    for all twelve, which is the price of never offering a dead one.
    """
    reach = FULL_CHAIN if max_priced_steps is None else max_priced_steps
    out = []
    for code in available_targets(graph, allowed):
        try:
            view = build_view(observations, fx, graph, target=code,
                              max_priced_steps=reach)
        except Exception:
            continue
        if target_markets(view):
            out.append(code)
    return out


def render_study(observations: Sequence[dict[str, Any]], fx: FxTable, graph: ChemicalRouteGraph,
                 *, allowed: frozenset[str] | None = None, max_priced_steps: int = FULL_CHAIN,
                 reference: Sequence[dict[str, Any]] | None = None,
                 imports: dict[str, Any] | None = None,
                 events: Sequence[dict[str, Any]] | None = None) -> None:
    """The whole section: pick a raw material, then its chemistry, its sources and what they show.

    Five sections, one population. Every count below the diagram is the set of chemicals in the route
    that carry a market price, and it is the same set in every one of them. The page used to hold
    five sections counting four different things - chemicals, process segments, edges, and whatever
    the two depth controls were set to - with nothing saying how any of them related, which is what
    made a correct page read as a contradictory one.
    """
    import streamlit as st

    st.markdown("## Chemical Relationships")

    for message in import_warnings(imports):
        st.warning(message)

    targets = targets_with_a_study(observations, fx, graph, allowed=allowed)
    if not targets:
        st.info("No chemical relationship view is available within the active entitlement.")
        return

    labels = {display_name(graph, code): code for code in targets}
    if len(labels) == 1:
        chosen = next(iter(labels))
        st.caption(f"Raw material: {chosen}")
    else:
        chosen = st.selectbox("Raw material", list(labels), key="relationship_target")
    target = labels[chosen]

    # No market selector, no depth control, no reach control. Nothing on this page varies by market
    # any more: the chemistry is the chemistry, and an announcement carries the market it was made in
    # on its own row. The measurement that did vary by market moved to Platform Admin with its
    # controls, and a selector that changes nothing a reader can see is worse than no selector.
    _render_route(graph, target)
    render(build_view(observations, fx, graph, target=target, max_priced_steps=max_priced_steps,
                      reference=reference), events=events, observations=observations)


def _render_route(graph: ChemicalRouteGraph, target: str) -> None:
    """How the raw material is made. Established chemistry, so it leads the page."""
    import pandas as pd
    import streamlit as st

    node = graph.node(target)
    name = node.display_name if node else target
    st.markdown("### How it is made")

    summary = route_summary(graph, target, max_chemical_steps=FULL_CHAIN)
    if not summary["chemicals"]:
        st.info(f"No production route is recorded for {name} yet.")
        return

    origins = ", ".join(summary["roots"]) or "not recorded"
    # One countable unit: chemicals. The process segments are real and they are in the table below as
    # a column; as a second headline number they only invited the reader to reconcile 18 against 29.
    st.caption(
        f"{summary['chemicals']} chemicals make {name}, starting from {origins}. "
        f"{summary['unpriced']} of them are never quoted anywhere — they are made and consumed "
        f"inside a plant, so the chain runs through them and no price signal can. The other "
        f"{summary['priced']} carry a market price, and those {summary['priced']} are what every "
        "table below this one counts."
    )
    tested = {c.chemical: c.priced_steps
              for c in graph.upstream_candidates(target, max_priced_steps=FULL_CHAIN)}
    st.graphviz_chart(route_dot(graph, target, max_chemical_steps=FULL_CHAIN, tested=tested),
                      width="stretch")

    with st.expander(f"The route as a list — all {summary['chemicals']} chemicals"):
        st.caption(
            "The same picture, one row per chemical, so this list and the sentence above it count "
            "the same thing. Which process makes it and which route it belongs to are columns here."
        )
        st.dataframe(pd.DataFrame(route_chemicals_table(graph, target,
                                                        max_chemical_steps=FULL_CHAIN)),
                     hide_index=True, width="stretch")


def holding_rows(view: RelationshipView) -> list[dict[str, Any]]:
    """What is held for each priced chemical in the route, one row each.

    The series names are on the row rather than in a separate list further down. Two tables about the
    same chemicals, in different orders, is how the old page ended up with an "individual series"
    section that nobody could say what it added.
    """
    return [
        {
            "Chemical": r.display_name,
            "Steps upstream": r.priced_steps,
            "What we hold": ", ".join(r.series_labels) or "nothing yet",
            "Months held": r.months_held,
            "Overlapping months": r.best_overlap,
            "Status": {
                "ready": "Firm", "testable": "Testable now", "short_history": "Very short",
                "too_short": "Too short to measure", "no_overlap": "No overlap",
                "missing": "No data",
            }.get(r.status, r.status),
            "What is needed": r.note,
        }
        for r in view.requirements
    ]


def render(view: RelationshipView, *, events: Sequence[dict[str, Any]] | None = None,
           observations: Sequence[dict[str, Any]] | None = None, fx: FxTable | None = None) -> None:
    """What the customer sees below the chemistry: dated producer announcements, and nothing else.

    The measurement used to sit here. Stefan's call, 20 September 2026: the series behind it carry no
    value yet, so there is no reason to put them in front of a customer. Of the twenty-one chemicals
    that make polyether polyol we hold a usable price for seven, six of those are United States
    producer-price indices standing in for chemicals priced in China, and every figure the study
    produced for China spans zero. A table of numbers that cannot fix their own direction is not
    intelligence, and printing it with a range attached does not make it one.

    It stays measured, on Platform Admin, where the question is whether the method works. It comes
    back to this page when there is enough data for the answers to hold and when those answers are
    shown to improve a forecast - and then how to show them is a decision to take again, from the
    evidence at that point rather than from this layout.
    """
    render_announcements(view, events=events, observations=observations)


def render_announcements(view: RelationshipView, *, events: Sequence[dict[str, Any]] | None = None,
                         observations: Sequence[dict[str, Any]] | None = None) -> None:
    """Producer price announcements for the raw material and for the chemicals that make it.

    A producer raising propylene oxide is news for every polyether polyol made from it, so the chain
    is asked for rather than the one chemical. An announcement is dated and attributable, so it
    answers the pricing question directly where a correlation answers it statistically.
    """
    import pandas as pd
    import streamlit as st

    st.markdown("### Producer price announcements")
    chain = [view.target] + [r.chemical for r in view.requirements]
    rows = announcement_timing(events or (), observations or (), materials=chain)
    summary = summarise_announcements(rows)
    if rows:
        st.caption(
            f"{summary['announcements']} announcement(s) across {view.target_label} and the "
            "chemicals that make it, with what the market price did around each one. "
            + str(summary.get("reading", ""))
        )
        st.dataframe(timing_frame(rows), hide_index=True, width="stretch",
                     height=38 * (len(rows) + 1))
    else:
        st.caption(
            f"No producer price announcement is held for {view.target_label} or for any chemical "
            "that makes it. The event lane currently captures plant events and force majeure "
            "notices for this chain."
        )


def render_method_evidence(view: RelationshipView, *, events: Sequence[dict[str, Any]] | None = None,
                           observations: Sequence[dict[str, Any]] | None = None,
                           fx: FxTable | None = None) -> None:  # noqa: C901
    """The evidence that the measurement is sound, for Platform Admin.

    None of this is a result. It is the working behind the results: which series each number was
    built from, whether the chain's steps add up to the direct measurement, whether the raw material
    moves ahead of its own feedstock, and how far the weekly lane has filled. It sat on the customer
    page, where it read as a page arguing with itself.
    """
    import pandas as pd
    import streamlit as st

    where = f" in {view.target_geography}" if view.target_geography else ""
    st.markdown(f"#### Does {view.short_name}{where} move with what makes it?")
    st.caption(
        "Held back from the customer page on 20 September 2026. The series behind these numbers "
        "carry no value yet - six of the seven usable ones are United States producer-price indices "
        "standing in for chemicals priced in China - and every figure for China spans zero. This is "
        "where the method is judged until the answers hold and are shown to improve a forecast."
    )
    st.info(headline(view))
    rows = measured_rows(view)
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                     height=38 * (len(rows) + 1))
    else:
        st.caption(f"Nothing shares enough months with {view.short_name} to be measured yet.")

    st.markdown(f"#### What we hold for each chemical that makes {view.short_name}")
    if view.requirements:
        st.caption(
            f"One row for each of the {view.upstream_total} chemicals the diagram shows as quoted. "
            "**Overlapping months** is what decides whether a chemical can be used at all: months "
            f"where it and {view.short_name} both have a price."
        )
        st.dataframe(pd.DataFrame(holding_rows(view)), hide_index=True, width="stretch",
                     height=38 * (len(view.requirements) + 1))

    st.markdown("#### Every pairing, not only the best-evidenced one")
    st.caption(
        "The customer page shows one row per chemical: the pairing with the most overlapping "
        "months. This is all of them, every driver series against every series of the raw material."
    )
    every = relationship_rows(view)
    if every:
        st.dataframe(pd.DataFrame(every), hide_index=True, width="stretch",
                     height=38 * (len(every) + 1))
    else:
        st.caption("No pairing shares any months yet.")

    st.markdown("#### The individual series behind this")
    st.markdown(f"**{view.target_label}'s own price history**")
    target_rows = target_series_rows(view)
    if target_rows:
        st.dataframe(pd.DataFrame(target_rows), hide_index=True, width="stretch",
                     height=38 * (len(target_rows) + 1))
    else:
        st.caption(f"No price history is held for {view.target_label} itself.")

    st.markdown("**Upstream series**")
    st.caption(
        "A benchmark price is money for material. An index is a level with a base year, not a "
        "price, and its months are only worth as much as the months it shares above."
    )
    upstream = upstream_series_rows(view)
    if upstream:
        st.dataframe(pd.DataFrame(upstream), hide_index=True, width="stretch",
                     height=38 * (len(upstream) + 1))
    else:
        st.caption("No upstream series are held for this chain yet.")

    st.markdown("#### Step by step down the chain")
    st.caption(
        "The chain is sequential, so testing every chemical against the raw material reports one "
        "signal travelling down it as several separate findings. Each row is one step between two "
        "chemicals somebody quotes, with the captive intermediates it passes through named but not "
        "measured."
    )
    link_table = link_rows(view.links, view.graph) if view.links and view.graph else []
    if link_table:
        st.dataframe(pd.DataFrame(link_table), hide_index=True, width="stretch",
                     height=38 * (len(link_table) + 1))
        consistency = lag_consistency(view.links, view.results, view.graph, view.target)
        if consistency:
            st.markdown("#### Do the lags add up?")
            st.caption(
                "If each step is real transmission, walking the chain should take about as long as "
                "measuring the two ends directly. A large difference would mean the direct "
                "correlation is picking up something other than cost passing down this chain."
            )
            st.dataframe(pd.DataFrame(consistency), hide_index=True, width="stretch",
                         height=38 * (len(consistency) + 1))
    else:
        st.caption("No step of this chain has two quoted chemicals with history on both sides yet.")

    render_who_moves_first(view, events=events, observations=observations, fx_table=fx)

    st.markdown("#### Weekly resolution — what it needs")
    st.caption(
        f"Weekly belongs to a market, not to a chemical. A chemical reads weekly below when at "
        f"least one of its markets holds {MIN_SERIES_WEEKS} weeks with at least "
        f"{MIN_WEEKLY_DENSITY:.0%} of the weeks in its own span carrying a price. The finished "
        "products have been assessed weekly for years; the feedstocks under them are still "
        "mostly monthly. A pair is limited by its coarser side, so a weekly product against a "
        "monthly feedstock is still a monthly answer — which is why everything customer-facing "
        "stays monthly even where this table says weekly. Weekly end to end needs both sides, "
        "and the feedstock rows are where that is filling up. Markets printing every business "
        "day are counted separately and then read as weekly: nothing is correlated daily, "
        "because two assessors disagree in the last digit on any given day and a week averages "
        "that out. That column is cadence, not readiness. This is every "
        "family in the store, not this chain: it reports how the weekly lane is filling up, "
        "which is a build question."
    )
    try:
        readiness = weekly_readiness(observations or (), fx, view.graph) if (view.graph and fx) else None
    except Exception:
        readiness = None
    if readiness and readiness["coverage"]:
        st.dataframe(pd.DataFrame([
            {"Chemical": row["chemical"].replace("_", " ").title(),
             "Markets": row["markets"],
             "Prints daily": row["daily_markets"],
             "Weekly markets": row["weekly_markets"],
             "Weeks held (best market)": row["weeks_held"],
             "Weeks in its span": row["weeks_in_span"],
             "Density (best market)": row["density"],
             "Weeks still needed": row["weeks_short"],
             "Status": row["reading"]}
            for row in readiness["coverage"]]), hide_index=True, width="stretch",
            height=38 * (len(readiness["coverage"]) + 1))

def render_who_moves_first(view: RelationshipView, *, events=None, observations=None,
                           fx_table=None) -> None:
    """Cost passing through, or a producer pricing on the news.

    A producer who watches crude and raises the price on the expectation of dearer feedstock moves
    before the cost arrives. To a monthly correlation that is indistinguishable from fast cost
    transmission - both land in month zero - so the correlation is shown for what it is worth and the
    announcements are shown beside it, because an announcement is that behaviour made visible and
    needs no sample size.
    """
    import pandas as pd
    import streamlit as st

    st.markdown("### Who moves first?")
    st.caption(
        "The measurement above asks only whether the feedstock leads, because that is the cost "
        "question. This asks the other one: does the raw material move ahead of its feedstock, which "
        "is what a producer pricing on expected cost rather than paid cost looks like."
    )

    if view.graph and view.target_series:
        against = max(view.target_series, key=lambda s: s.months)
        drivers = [s for s in view.series if s.chemical != view.target]
        diagnostics = [lead_diagnostic(against, driver) for driver in drivers]
        if diagnostics:
            st.dataframe(pd.DataFrame(lead_rows(diagnostics, view.graph)), hide_index=True,
                         width="stretch", height=38 * (len(diagnostics) + 1))
            st.caption(
                "Searching both directions doubles the chances of a coincidence looking impressive, "
                "which is why this is a diagnostic and never the headline. A row that cannot pick a "
                "side is the honest answer, not a failure: at monthly resolution a producer passing "
                "cost through quickly and one pricing on the news land in the same month."
            )

    st.markdown("##### Producer price announcements")
    rows = announcement_timing(events or (), observations or (), material=view.target)
    summary = summarise_announcements(rows)
    if rows:
        st.dataframe(timing_frame(rows), hide_index=True, width="stretch",
                     height=38 * (len(rows) + 1))
    st.caption(
        f"{summary['announcements']} announcement(s) held for {view.target_label}. "
        + str(summary.get("reading", ""))
    )
    if not rows:
        st.caption(
            "An announcement is dated and attributable, so it answers the question directly rather "
            "than statistically — but only once the store holds some. None is collected for this "
            "raw material yet; the event lane currently captures plant events and force majeure "
            "notices for it, and producer price announcements for other chemicals."
        )

