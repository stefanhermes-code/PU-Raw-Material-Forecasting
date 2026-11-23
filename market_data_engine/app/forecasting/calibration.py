"""Making the range mean what it says.

A model's interval is a claim: "the price lands inside this about eight times in ten". That claim was
never checked. It was measured on TDI China on 13 September 2026, by rolling the origin forward one
month at a time and refitting at each one, and the answer was:

    1 month ahead   9 of 16 inside   56%
    2 months        9 of 15 inside   60%
    3 months        7 of 14 inside   50%

Against a claim of 80%. The page was telling a buyer the price would stay in a range that in fact
held about half the time - and the further out, the worse it got, which is the opposite of what a
reader assumes when a band widens.

The cause is not a bug in the quantiles. A model's residuals are the errors it made on the data it
was fitted to, so they are systematically smaller than the errors it makes on data it has not seen.
Building the band from out-of-sample errors instead was tried and covered no better: on a 28-point
series a 10th and a 90th percentile rest on two or three observations either way, and short samples
under-represent exactly the large moves the tail is supposed to describe.

So the width is calibrated rather than asserted. The scale that would have delivered the target
coverage on this series' own rolling-origin backtest is searched for, and applied. On TDI China that
came out near 2.0 - the honest range is about twice the one the page was showing.

What this is NOT: a probability. It is the multiplier that would have worked on the history we hold,
measured on sixteen origins at one month and fewer further out. `coverage` and `origins` travel with
it so the page can say how much evidence the claim rests on, and say nothing when there is too
little. A number with its sample size beside it can be argued with; one without cannot.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

# The interval the application shows: 10th to 90th percentile.
TARGET_COVERAGE = 0.80

# Scales searched, narrowest first. Below 1.0 is not offered: a model that is already overconfident
# is never made more so by this.
SCALE_GRID: tuple[float, ...] = (1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 3.0, 3.5, 4.0)

# Kept for the callers that still report it. It no longer decides whether a market gets a forecast.
#
# It used to: below eight trials the calibration returned nothing, the banner had no bands, and the
# page said the forecast was "being prepared" - indefinitely, for China MDI, on twelve months of
# real readings. A threshold cannot be right when nothing happens at seven trials and a confident
# claim appears at eight. What actually changes with the count is how precisely the coverage can be
# stated, and that is arithmetic: see `defensible_coverage`. So the band is always cut, and the page
# prints the certainty the trials can carry.
MIN_ORIGINS = 8

# One-sided confidence on the coverage a band can be claimed to have.
#
# A band cut so that 80% of past errors fall inside it has an observed coverage of 80% by
# construction. How much that is worth depends entirely on how many trials it was cut from: eight
# trials cannot tell 80% from 55%. The lower bound of a one-sided interval answers exactly that, and
# it is what goes on the page - so the same band reads 80% on a long history and 52% on a short one,
# with nobody deciding what counts as enough.
COVERAGE_CONFIDENCE = 0.90
_Z = {0.80: 0.8416, 0.90: 1.2816, 0.95: 1.6449}


def wilson_lower(inside: int, trials: int, z: float) -> float:
    """Lower bound of the Wilson score interval - closed form, no dependency, standard.

    Chosen over the normal approximation because that one misbehaves at small counts, which is the
    only place this is interesting.
    """
    if trials <= 0:
        return 0.0
    hit = inside / trials
    z2 = z * z
    denominator = 1.0 + z2 / trials
    centre = (hit + z2 / (2 * trials)) / denominator
    spread = z * ((hit * (1.0 - hit) / trials + z2 / (4 * trials * trials)) ** 0.5) / denominator
    return max(0.0, centre - spread)


def defensible_coverage(trials: int, coverage: float,
                        confidence: float = COVERAGE_CONFIDENCE) -> float | None:
    """The coverage a band cut at `coverage` can be claimed to have, given how often it was tested.

    Returns a fraction, or None when nothing has been tested at all. Zero trials is not a small
    number; it is no evidence, and the page has to say so in words rather than print a nought.
    """
    if trials < 1:
        return None
    # Floor, not round. Rounding up let four trials claim more than eight: cutting at 90% of four
    # rounds to 4 of 4, a perfect record on nothing. The floor is the count the band is certain to
    # have covered, so the answer only ever improves with evidence.
    inside = int(coverage * trials)
    return round(wilson_lower(inside, trials, _Z.get(confidence, _Z[0.90])), 4)

# How close the forecast landed, as tolerances around the forecast itself.
#
# Coverage answers "did the price stay inside a band we drew wide on purpose", which is a question
# about the band. A reader looking at a number wants to know how often that number was close, and
# these answer it: the share of past forecasts that landed within 10% and within 15% of what the
# price actually did. Ten and fifteen were settled with Stefan on 15 September 2026 - twenty was
# too loose to mean anything on this market.
HIT_TOLERANCES: tuple[float, ...] = (0.10, 0.15)

# The probabilities the banner prints a band for. Whole numbers a person asked for, not whatever a
# grid of scale factors happened to land on.
BAND_COVERAGES: tuple[float, ...] = (0.80, 0.90)

# The shortest history a fit is attempted on while rolling the origin.
#
# Twelve was mine, copied from a model that fits several coefficients and an intercept, and it meant
# China MDI had twelve monthly readings and zero trials - the training minimum ate the whole series
# and the page showed no forecast rather than a weakly tested one.
#
# Four is the arithmetic floor for the card model: one coefficient through the origin needs two
# pairs before the data can disagree with it, and two pairs need four readings. Below that the fit
# is exact and tells you nothing.
#
# Trials on short histories are not noise dressed as evidence, because the model shrinks a carry it
# cannot measure to zero and hands back the last price. An early origin therefore tests naive, which
# is a real thing to test, rather than a wild coefficient off three points.
MIN_TRAIN = 4


@dataclass(frozen=True)
class Calibration:
    steps: int
    scale: float
    coverage: float | None      # share inside the band at the chosen scale, or None when untested
    inside: int
    origins: int
    target: float = TARGET_COVERAGE
    # (tolerance, share) per HIT_TOLERANCES, measured on the same origins as the coverage above.
    hits: tuple[tuple[float, float], ...] = ()
    # (coverage, low, high) per BAND_COVERAGES, as fractions of the forecast. The band the page
    # prints, measured off the same origins.
    bands: tuple[tuple[float, float, float], ...] = ()

    @property
    def tested(self) -> bool:
        """Whether the band rests on anything at all.

        This used to mean "eight trials or more", and below it the page showed no forecast. One
        trial is not eight, but it is not nothing either, and the difference between them is now
        carried by `certainty` rather than by hiding the answer. Tested means tested.
        """
        return self.origins >= 1

    def as_dict(self) -> dict:
        return {"steps": self.steps, "scale": round(self.scale, 2),
                "coverage": round(self.coverage, 3) if self.coverage is not None else None,
                "inside": self.inside, "origins": self.origins, "target": self.target,
                "tested": self.tested,
                # Keyed by the tolerance as whole percent - "10", "15" - because that is how the
                # banner column is headed and a float key does not survive JSON.
                "hits": {f"{tolerance * 100:.0f}": round(share, 3)
                         for tolerance, share in self.hits},
                "bands": {f"{coverage * 100:.0f}": [round(low, 4), round(high, 4)]
                          for coverage, low, high in self.bands},
                # What each band can be claimed to cover, given how often it was tested. Keyed the
                # same way as `bands`, so the banner reads the label off the band it is printing.
                "certainty": {f"{coverage * 100:.0f}": defensible_coverage(self.origins, coverage)
                              for coverage, _low, _high in self.bands}}


@dataclass(frozen=True)
class Origin:
    """One rolling-origin trial: what the model said, and what then happened."""
    centre: float
    low_offset: float       # the model's own band around its forecast, AT THIS HORIZON
    high_offset: float
    actual: float


def origins_for(items: Sequence, steps: int, fit: Callable, min_train: int = MIN_TRAIN,
                actual_of: Callable = lambda item: item,
                available_of: Callable | None = None) -> list[Origin]:
    """Roll the origin forward, refitting at each one.

    The fit does not depend on the scale being searched, so every origin is fitted ONCE and every
    candidate scale is then judged against the same set. Fitting inside the scale loop would run the
    model ten times over for no new information.

    `fit` takes a training window and returns (centre, low_offset, high_offset), or None when the
    window is too short for that model. Passing the model in keeps this free of its internals.

    `available_of` is what stops the backtest reading the future. Without it the window is simply
    everything dated before the origin - which is every month a publisher has SINCE printed, not the
    months that existed when the forecast would have been made. Eurostat Comext runs two months
    behind and UN Comtrade three, so a June origin was being handed June's price that did not appear
    until August or September. 100 of the 120 customer-visible series carry a lag of that kind, and
    a band calibrated on errors a forecaster could never have made comes out too narrow: measured on
    polyol Europe, an eighty per cent band held about two thirds.

    Given `available_of`, the origin is the moment the last reading in the window actually arrived,
    and the window is what had arrived by then. Readings referring to earlier months that landed
    later are correctly excluded.

    This fixes what the model is TRAINED on. It does not change what `steps` means: the target is
    still that many periods past the last reading held, not that many months past today. Where a
    series publishes late those are different questions, and the second one is a product decision
    rather than a defect.
    """
    out: list[Origin] = []
    for cut in range(min_train, len(items) - steps + 1):
        window = list(items[:cut])
        if available_of is not None:
            standing = available_of(items[cut - 1])
            if standing is None:
                continue
            window = [item for item in window
                      if (moment := available_of(item)) is not None and moment <= standing]
            if len(window) < min_train:
                continue
        fitted = fit(window)
        if fitted is None:
            continue
        centre, low_offset, high_offset = fitted
        out.append(Origin(centre, low_offset, high_offset, float(actual_of(items[cut + steps - 1]))))
    return out


def coverage_at(origins: Sequence[Origin], scale: float) -> tuple[int, int]:
    """(inside, tested) for one scale.

    The offsets are the band the model produced AT THAT HORIZON, so they already carry however that
    model widens with distance - ETS widens by the square root of the horizon. Re-applying that here
    would widen it twice. This scale corrects the overall level and nothing else.
    """
    inside = sum(
        1 for origin in origins
        if origin.centre + origin.low_offset * scale <= origin.actual
        <= origin.centre + origin.high_offset * scale
    )
    return inside, len(origins)


def hit_rates(origins: Sequence[Origin],
              tolerances: Sequence[float] = HIT_TOLERANCES) -> tuple[tuple[float, float], ...]:
    """How often the forecast itself landed within each tolerance of what happened.

    Measured against the actual, not against the forecast, so a market that halved would show a
    forecast 100% out rather than 50%. The share is of origins that produced an actual; an origin
    with none is not counted either way.
    """
    usable = [origin for origin in origins if origin.actual]
    if not usable:
        return ()
    out: list[tuple[float, float]] = []
    for tolerance in tolerances:
        hits = sum(1 for origin in usable
                   if abs(origin.centre - origin.actual) / abs(origin.actual) <= tolerance)
        out.append((float(tolerance), hits / len(usable)))
    return tuple(out)


def _percentile(values: Sequence[float], share: float) -> float:
    """Linear interpolation between the two nearest observations, as every percentile does."""
    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * share
    below, above = int(position // 1), min(int(position // 1) + 1, len(ordered) - 1)
    return ordered[below] + (ordered[above] - ordered[below]) * (position - below)


def error_band(origins: Sequence[Origin], coverage: float) -> tuple[float, float] | None:
    """(low, high) as fractions of the forecast, from the errors the model actually made.

    This replaces a band the model drew around itself. That one was built from the residuals of its
    own fit - errors on data it had already seen - so it came out too narrow, and a scale factor
    then multiplied it until its backtested coverage reached the target. Two wrongs in series: on
    TDI China the printed 80% band was 2.25 times the model's own and ran from -24% to +26%, where
    the errors the model actually made put 80% of outcomes inside -14.2% to +14.4%.

    Here the band IS the errors. Roll the origin, record how far each forecast landed from what
    happened, and cut the distribution at the two tails that leave `coverage` in the middle. An 80%
    band is the 10th to the 90th percentile of those errors and needs no correcting, because it is
    measured rather than assumed.

    Returned as fractions of the forecast so a band measured on a market at 12,000 still applies at
    17,000.
    """
    errors = [(origin.actual - origin.centre) / origin.centre
              for origin in origins if origin.centre]
    if not errors:
        return None
    tail = (1.0 - coverage) / 2.0
    low, high = _percentile(errors, tail), _percentile(errors, 1.0 - tail)
    # The band always contains the forecast. When every recorded miss fell on one side - Adipic
    # Acid China, 17 October 2026: two trials, both 5.3-5.5% low - the cut lands entirely above
    # the forecast and the card printed a forecast outside its own band. A band that excludes its
    # centre is a bias measurement, not a range a buyer can act on; the centre is included and the
    # bias stays visible as a lopsided band.
    return min(low, 0.0), max(high, 0.0)


def calibrate(items: Sequence, steps: int, fit: Callable, target: float = TARGET_COVERAGE,
              min_train: int = MIN_TRAIN, actual_of: Callable = lambda item: item,
              available_of: Callable | None = None) -> Calibration:
    """The narrowest scale whose backtested coverage reaches the target.

    Narrowest, not best: a wider band always covers more, so without stopping at the target the
    search would run to the end of the grid and hand back a range too wide to be worth reading.

    When nothing on the grid reaches the target, the widest is returned WITH its measured coverage,
    so the page reports what the band actually achieves rather than what was hoped for. When there
    are too few origins to measure at all, the band is left exactly as the model made it and marked
    untested - a coverage figure off four trials is not evidence, and dressing one up as though it
    were is the fault this module exists to fix.
    """
    trials = origins_for(items, steps, fit, min_train, actual_of, available_of)
    if not trials:
        return Calibration(steps, 1.0, None, 0, 0)
    # Measured once on the same origins the coverage search uses. The hit rate is a property of the
    # forecast, so no scale changes it.
    hits = hit_rates(trials)
    bands = tuple((coverage,) + band for coverage in BAND_COVERAGES
                  if (band := error_band(trials, coverage)) is not None)
    best: Calibration | None = None
    for scale in SCALE_GRID:
        inside, tested = coverage_at(trials, scale)
        best = Calibration(steps, scale, inside / tested, inside, tested, hits=hits, bands=bands)
        if best.coverage is not None and best.coverage >= target:
            return best
    return best or Calibration(steps, 1.0, None, 0, len(trials), hits=hits, bands=bands)


def apply(quantiles: dict, centre: float, scale: float) -> dict:
    """Widen a band around its own centre, leaving the forecast where it is."""
    if scale == 1.0 or not quantiles:
        return quantiles
    return {key: round(centre + (float(value) - centre) * scale, 2)
            for key, value in quantiles.items()}
