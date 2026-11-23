"""A forecast that asserts a move no market in this store has ever made.

WHY THIS EXISTS. On 18 November 2026 the vintage store held 135 forecasts whose uncertainty band
reached a NEGATIVE price, and 15 of them were in the released lane - the lane a card draws from.
Every one of the fifteen was China diethylene glycol or benzene, dated between 1 and 9 November,
with a tenth-percentile band between -531 and -1,614 CNY/MT against a last price near 6,400. The
Forecast page drew that band every day for a week. A price cannot be negative, and nothing in the
engine said so.

The central estimates are in better shape: 63 of 8,749 sit outside the band below and not one of
them was ever released. So this guard is mostly a prevention rather than a cleanup - but 63 proves
the models do produce such numbers, and the only thing that kept them off the page was which lane
they happened to land in.

WHERE THE BAND COMES FROM. Not from a judgement about what a market can do. Measured off this
store's own 35,952 price observations, every reading paired with the reading nearest the horizon
ahead of it - about 31,000 to 35,000 pairs per horizon:

    horizon   1-in-1000 fall   1-in-1000 rise
       4w          0.56             2.05
       8w          0.50             2.26
      12w          0.46             2.47
      26w          0.43             3.00
      52w          0.37             3.17

The band below is wider than every one of those, so a forecast it refuses is asserting a move rarer
than one in a thousand of everything these markets have actually done since 1999.

WHAT IT DOES NOT DO. It does not clamp, scale or correct a number. A forecast outside the band is
refused and the reason is carried, the same way a model that raises is `failed` and a model with
nothing to say is `declined`. Rescuing a number by moving it would put a figure on the page that no
model produced, which is the one thing this application exists not to do.

IT ASKS ABOUT THE FORECAST, NOT ABOUT THE MARKET. A real market that moves 3x in a month is still
read, still stored and still charted - this never touches a price. What it refuses is a MODEL
claiming such a move in advance. Those are different assertions and only the second one is cheap to
be wrong about.

THE CENTRE AND THE BAND ARE JUDGED APART, on purpose. Refusing a whole model for an unusable band
would have emptied 136 of the 1,250 publishable cards in the vintage history, because on those
markets `momentum` was the only model that answered. A card showing a point estimate and no band is
honest and useful; a blank card where a forecast used to be is neither, and it would have been the
cost of being strict about the wrong half.
"""
from __future__ import annotations

# (horizon in weeks, lowest publishable multiple of the anchor, highest). Each row applies from its
# own horizon up to the next. Measured: see WHERE THE BAND COMES FROM.
_BAND: tuple[tuple[int, float, float], ...] = (
    (4, 0.40, 2.50),
    (8, 0.36, 2.75),
    (12, 0.33, 3.00),
    (26, 0.29, 3.60),
    (52, 0.25, 4.00),
)


def band(horizon_weeks: int) -> tuple[float, float]:
    """The lowest and highest multiple of the last price a forecast at this horizon may assert.

    Below the shortest measured horizon the tightest band applies, and above the longest the widest
    does: a horizon nobody measured does not get a licence to say anything.
    """
    low, high = _BAND[0][1], _BAND[0][2]
    for weeks, lower, upper in _BAND:
        if horizon_weeks >= weeks:
            low, high = lower, upper
    return low, high


def refuse_value(value, anchor, horizon_weeks: int) -> str | None:
    """Why this central estimate is not publishable, or None if it is.

    An anchor of zero or None means there is nothing to judge against, and inventing a yardstick
    would be worse than not having one, so such a forecast passes untouched.
    """
    if value is None or anchor in (None, 0):
        return None
    value, anchor = float(value), float(anchor)
    if value <= 0:
        return f"the forecast is {value:,.2f}, and a price cannot be zero or negative"
    low, high = band(horizon_weeks)
    ratio = value / anchor
    if ratio < low or ratio > high:
        return (f"the forecast is {ratio:.2f} times the last price ({value:,.2f} against "
                f"{anchor:,.2f}) at {horizon_weeks} weeks, outside the {low:.2f}-{high:.2f} "
                f"a move rarer than one in a thousand in this store's own history")
    return None


def usable_quantiles(quantiles, anchor, horizon_weeks: int) -> tuple[dict, str | None]:
    """The band with nothing impossible left in it, and why anything went.

    All or nothing per point. A band is a pair of numbers that belong together, and keeping the half
    that happens to be plausible would narrow the interval rather than report that it is unusable -
    a quieter and more flattering lie than the negative number it replaced.
    """
    if not quantiles or anchor in (None, 0):
        return (dict(quantiles) if quantiles else {}), None
    anchor = float(anchor)
    low, high = band(horizon_weeks)
    for level, value in quantiles.items():
        if value is None:
            continue
        value = float(value)
        if value <= 0:
            return {}, (f"the {level} band is {value:,.2f}, and a price cannot be zero or negative; "
                        f"the interval is withheld and the forecast stands without one")
        ratio = value / anchor
        if ratio < low or ratio > high:
            return {}, (f"the {level} band is {ratio:.2f} times the last price at {horizon_weeks} "
                        f"weeks, outside the {low:.2f}-{high:.2f} this store's own history "
                        f"supports; the interval is withheld and the forecast stands without one")
    return dict(quantiles), None
