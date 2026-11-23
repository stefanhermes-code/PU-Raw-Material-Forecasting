"""W3 and W4, answered from the 34,000 readings we collected ourselves.

The one source the old pipeline never consulted was itself. An anonymous report of a producer
raising prices was rejected as unattributable while our own three independent China polyol series
sat in the same database showing the market up 6.5% that week.

Two questions are answered here.

W3, the trace: did our own series move, around the time of the observation, by more than it usually
moves? That is first-party, daily and attributable to us. Nobody has to be named and nothing has to
be corroborated, because it is our own reading.

W4, the implication: a move of a given size implies roughly how much supply came off line, which
the roster can check against each candidate's capacity. The arithmetic is deliberately crude - an
elasticity nobody can measure honestly is not improved by giving it decimals - so it reports a
range and says what it assumed.
"""
from __future__ import annotations

import statistics as st
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import MarketIdentityModel, PriceObservationModel

# How far either side of the observation to look. A producer announcement shows up in an assessment
# within days; a plant event can take a fortnight to work through.
BEFORE_DAYS = 21
AFTER_DAYS = 21

# What counts as a move worth reporting: more than twice the series' own typical weekly change.
# Measured against the series itself, so a volatile Chinese spot market and a quarterly European
# contract are each judged by their own standard.
UNUSUAL = 2.0


def _series(session: Session, chemical: str, geography: str) -> list[tuple[datetime, float]]:
    """The best-covered basis for this market, as a dated series in the stored currency."""
    bases = session.execute(
        select(MarketIdentityModel.commercial_basis)
        .where(MarketIdentityModel.product_family == chemical)
        .where(MarketIdentityModel.market_geography == geography).distinct()).scalars().all()
    best: list[tuple[datetime, float]] = []
    for basis in bases:
        rows = session.execute(
            select(PriceObservationModel.observation_date, PriceObservationModel.price)
            .join(MarketIdentityModel, MarketIdentityModel.id == PriceObservationModel.identity_id)
            .where(MarketIdentityModel.product_family == chemical)
            .where(MarketIdentityModel.market_geography == geography)
            .where(MarketIdentityModel.commercial_basis == basis)
            .where(PriceObservationModel.price.isnot(None))).all()
        bucket: dict[datetime, list[float]] = {}
        for when, price in rows:
            if when is None:
                continue
            # SQLite hands back naive datetimes whatever the column says, so everything is put on
            # UTC here rather than at each comparison.
            day = when.replace(hour=0, minute=0, second=0, microsecond=0)
            if day.tzinfo is None:
                day = day.replace(tzinfo=timezone.utc)
            bucket.setdefault(day, []).append(float(price))
        found = sorted((day, st.median(values)) for day, values in bucket.items())
        if len(found) > len(best):
            best = found
    return best


def trace(session: Session, chemical: str, geography: str, around) -> dict:
    """Did our own series move around this date, and was the move unusual for that series?

    Returns what it found in full, including the case where it found nothing - "the series did not
    move" is an answer, and a series we do not hold is a different answer again.
    """
    series = _series(session, chemical, geography)
    if not series:
        return {"held": False, "why": f"we hold no price series for {chemical} in {geography}"}
    when = around if isinstance(around, datetime) else datetime.fromisoformat(str(around)[:10])
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    first, last = when - timedelta(days=BEFORE_DAYS), when + timedelta(days=AFTER_DAYS)
    window = [(d, p) for d, p in series if first <= d <= last]
    if len(window) < 2:
        return {"held": True, "in_window": len(window),
                "why": (f"the series exists but carries {len(window)} reading(s) between "
                        f"{first.date()} and {last.date()}, which cannot show a move")}

    # The series' own normal step, so each market is judged by its own standard.
    steps = [abs(b / a - 1) * 100 for (_d1, a), (_d2, b) in zip(series, series[1:]) if a]
    usual = st.median(steps) if steps else 0.0
    before = [p for d, p in window if d <= when]
    after = [p for d, p in window if d >= when]
    start = before[0] if before else window[0][1]
    end = after[-1] if after else window[-1][1]
    move = (end / start - 1) * 100 if start else 0.0
    return {"held": True, "in_window": len(window), "from": round(start, 2), "to": round(end, 2),
            "move_pct": round(move, 2), "usual_step_pct": round(usual, 3),
            "unusual": bool(usual and abs(move) > usual * UNUSUAL),
            "first_day": str(window[0][0].date()), "last_day": str(window[-1][0].date()),
            "why": (f"{start:,.0f} on {window[0][0].date()} to {end:,.0f} on "
                    f"{window[-1][0].date()}, {move:+.2f}%, against a typical step of "
                    f"{usual:.2f}% for this series")}


def capacity_implied(move_pct: float, *, regional_kt: float, elasticity: float = 0.5) -> dict:
    """Roughly how much supply a move of this size implies came off line.

    Deliberately crude, and says so. A price elasticity for a regional isocyanate market is not
    something anyone can measure honestly from the data we hold, so one number is assumed, named
    and reported with the range it implies. The purpose is only to ask whether a 200 kt plant or a
    50 kt plant fits better - a question a factor-of-two answer settles.
    """
    if not regional_kt or not move_pct:
        return {"implied_kt": None, "why": "no regional capacity on the roster, or no move"}
    share = abs(move_pct) / 100.0 * elasticity
    middle = regional_kt * share
    return {"implied_kt": round(middle, 1),
            "low_kt": round(middle * 0.5, 1), "high_kt": round(middle * 2.0, 1),
            "elasticity_assumed": elasticity, "regional_kt": regional_kt,
            "why": (f"a {move_pct:+.2f}% move on {regional_kt:,.0f} kt of regional capacity "
                    f"implies roughly {middle:,.0f} kt off line at an assumed elasticity of "
                    f"{elasticity}, so somewhere between {middle * 0.5:,.0f} and "
                    f"{middle * 2.0:,.0f} kt. A factor of two, which is enough to tell a 200 kt "
                    "plant from a 50 kt one and not enough for anything finer.")}


def fits(candidates: list[dict], implied: dict) -> list[dict]:
    """Which candidates' capacity sits inside the implied range. Nothing is ruled out on this alone."""
    low, high = implied.get("low_kt"), implied.get("high_kt")
    if low is None:
        return candidates
    out = []
    for one in candidates:
        kt = one.get("capacity_kt")
        out.append({**one, "fits_the_move": bool(kt and low <= kt <= high)})
    return out
