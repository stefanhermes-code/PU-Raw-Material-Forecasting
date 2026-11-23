"""The chart a page shows, drawn again as a picture for the Word report.

Stefan, 23 September 2026: the Current Market and Forecast pages get a report of their own, with the
last six months as a graph and the numbers under it.

Drawn with Pillow, which the application already has, rather than by adding a chart renderer to the
environment. What it draws is what the page draws: the market price, and on the forecast report the
forecast path with the range around it. Nothing is smoothed, interpolated or invented - a month with
no reading has no point.
"""
from __future__ import annotations

from raw_material_forecasting import units
from datetime import date, timedelta
from io import BytesIO

WIDTH, HEIGHT = 1650, 850
MARGIN_L, MARGIN_R, MARGIN_T, MARGIN_B = 150, 40, 40, 110

NAVY = (11, 42, 74)
BLUE = (29, 78, 216)
TEAL = (13, 148, 136)
BAND = (206, 233, 230)
# The feedstock line, and the wash behind the period the report is about.
AMBER = (180, 115, 20)
PERIOD = (241, 245, 249)
AHEAD = (237, 249, 248)
# The monitor's accent. Stefan's reference layout, 12 October 2026: the forecast is orange on a
# pale orange range, against the navy and blue of the record. The application's own pages keep the
# teal they have always had, so a chart drawn for a screen and a chart drawn for the report are
# told apart at a glance by someone holding both.
ORANGE = (197, 90, 17)
ORANGE_BAND = (250, 224, 196)
ORANGE_WASH = (253, 243, 233)
GRID = (226, 232, 240)
GREY = (92, 113, 132)
WHITE = (255, 255, 255)

_FONTS = ("calibri.ttf", "Calibri.ttf", "arial.ttf", "Arial.ttf", "DejaVuSans.ttf",
          "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


def _font(size: int):
    from PIL import ImageFont

    for name in _FONTS:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _as_date(value) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None


def _breaks(days: list, *, floor_days: int = 21, factor: float = 3.0) -> set[int]:
    """Where a line must stop, because the market stopped.

    A straight line across a hole is a price path that never happened. The 29 September Q3 report
    drew one from early May to late September - four and a half months - under a page that said a
    price is never carried forward to fill a gap.

    What counts as a hole is the series' own rhythm, not a fixed number of days: three times the
    usual interval between readings, and never less than three weeks, so a monthly publisher is not
    cut up for publishing monthly.
    """
    if len(days) < 3:
        return set()
    steps = [(days[i + 1] - days[i]).days for i in range(len(days) - 1)]
    overall = sorted(steps)[len(steps) // 2] or 1
    breaks = set()
    for i, step in enumerate(steps):
        # Each gap is judged against the rhythm the series was running at just before it, never
        # against the rhythm of the whole picture.
        #
        # Stefan, 10 October 2026, on the Q3 chart: twenty months of monthly readings came out as
        # loose dots. The median of the whole series was one day, because five weeks of daily
        # readings outnumbered twenty monthly ones, so the three-week floor cut every month apart.
        # His standing rule covers it - do not assume everything is daily and then call the rest
        # missing. A trailing window carries the cadence that was actually in force.
        def cadence(window):
            if len(window) < 3:
                return 0
            return sorted(window)[min(len(window) - 1, (len(window) * 3) // 4)]

        usual = max(cadence(steps[max(0, i - 8):i]), cadence(steps[i + 1:i + 9]), 0) or overall
        limit = max(usual * factor, floor_days)
        if step > limit:
            breaks.add(i)
    return breaks


def _runs(points: list, breaks: set[int]) -> list[list]:
    """The line split into the stretches that are actually continuous."""
    out, current = [], []
    for index, point in enumerate(points):
        current.append(point)
        if index in breaks:
            out.append(current)
            current = []
    if current:
        out.append(current)
    return [run for run in out if run]


def _nice_ticks(low: float, high: float, count: int = 5) -> list[float]:
    """Round values covering the range, so a reader reads a price and not an artefact."""
    if high <= low:
        high = low + 1.0
    span = (high - low) / max(count - 1, 1)
    step = 10 ** int(len(str(int(max(span, 1)))) - 1)
    for size in (1, 2, 2.5, 5, 10):
        if step * size >= span:
            step = step * size
            break
    start = int(low / step) * step
    ticks = []
    while start <= high + step / 2:
        ticks.append(start)
        start += step
    # Only the ticks that fall inside the range the data occupies. Extending the axis down to the
    # tick below the data is what left the bottom third of the Q3 chart empty under a series that
    # never goes near it.
    return [tick for tick in ticks if low - step / 2 <= tick <= high + step / 2] or ticks


def _month_ticks(first: date, last: date) -> list[tuple[date, str]]:
    """One label per month, written as the month and the year.

    A chart set two or three to a row has no room for a date every fortnight, and a reader of a
    quarterly report is placing a movement in a month rather than on a day. The days themselves
    are still in the caption under the chart.
    """
    out: list[tuple[date, str]] = []
    month = date(first.year, first.month, 1)
    while month <= last:
        if month >= first:
            out.append((month, month.strftime("%b %Y")))
        month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    if not out or out[0][0] > first:
        out.insert(0, (first, first.strftime("%b %Y")))
    # A window inside one month has one month label and no sense of where it runs from and to.
    # The days themselves are written at the ends, which is what a three-week market needs.
    if len(out) < 2:
        out = [(first, f"{first:%d %b}".lstrip("0")), (last, f"{last:%d %b}".lstrip("0"))]
    return out


def _date_ticks(first: date, last: date, span: int) -> list[tuple[date, str]]:
    """Where to write a date on the horizontal axis, and how to write it.

    The spacing follows how much time the picture covers, so a three-week window is dated by day
    and three years of history by quarter. The first and last days are always written: between
    them a reader can place every other point, and without them the window itself is a guess.
    """
    out: list[tuple[date, str]] = []
    if span <= 45:
        step = 3 if span <= 14 else 7
        when = first
        while when <= last:
            out.append((when, f"{when:%d %b}".lstrip("0")))
            when = when + timedelta(days=step)
    elif span <= 200:
        when = first
        while when <= last:
            out.append((when, f"{when:%d %b}".lstrip("0")))
            when = when + timedelta(days=14 if span <= 120 else 28)
    else:
        every = 1 if span <= 420 else 3
        month = date(first.year, first.month, 1)
        while month <= last:
            if month >= first and (month.month - 1) % every == 0:
                out.append((month, month.strftime("%b %y")))
            month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    held = {when: label for when, label in out if first <= when <= last}
    # The ends, whatever the spacing landed on, so the window is readable off the axis.
    for when in (first, last):
        held.setdefault(when, f"{when:%d %b %Y}".lstrip("0") if span > 200
                        else f"{when:%d %b}".lstrip("0"))
    return sorted(held.items())


def _dashed(draw, points, colour, width: int, on: int = 18, off: int = 12) -> None:
    carry = 0.0
    drawing = True
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        length = max(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5, 0.001)
        travelled = 0.0
        while travelled < length:
            step = (on if drawing else off) - carry
            step = min(step, length - travelled)
            a = travelled / length
            b = (travelled + step) / length
            if drawing:
                draw.line([(x1 + (x2 - x1) * a, y1 + (y2 - y1) * a),
                           (x1 + (x2 - x1) * b, y1 + (y2 - y1) * b)], fill=colour, width=width)
            travelled += step
            carry += step
            if carry >= (on if drawing else off):
                carry, drawing = 0.0, not drawing


def _label_points(draw, rows, px, py, colour, font, currency: str,
                  bounds: tuple[float, float] | None = None) -> list:
    """The open, the close, the high and the low, written on the picture.

    Stefan, 10 October 2026: the graph should show the numbers that are in the table above it.
    Four points carry a label, which is the four figures on the first page and no more, so the
    picture states them rather than leaving a reader to measure them off an axis.
    """
    if len(rows) < 2:
        return []
    values = [value for _day, value in rows]
    wanted: dict[int, str] = {}
    for index, name in ((0, "open"), (len(rows) - 1, "close"),
                        (values.index(max(values)), "high"), (values.index(min(values)), "low")):
        wanted.setdefault(index, name)
    drawn: list[tuple[float, float]] = []
    # Where each one landed and how wide it is, so nothing else is written on top of it.
    written: list[tuple[float, float, float]] = []
    for index in sorted(wanted):
        day, value = rows[index]
        x, y = px(day), py(value)
        said = f"{value:,.0f}"
        wide = draw.textlength(said, font=font)
        above = wanted[index] in ("high", "close")
        left, right = bounds or (MARGIN_L, WIDTH - MARGIN_R)
        lift = 34 if font.size >= 22 else 26
        at = (min(max(x - wide / 2, left + 2), right - wide),
              y - lift if above else y + lift * 0.42)
        if any(abs(at[0] - was[0]) < wide + 12 and abs(at[1] - was[1]) < 30 for was in drawn):
            continue
        drawn.append(at)
        written.append((at[0], at[1], wide))
        draw.text(at, said, fill=colour, font=font)
    return written


def price_chart_png(history, *, currency: str, forecast=None, band=None, title: str = "",
                    second=None, second_name: str = "", first_name: str = "",
                    period: tuple | None = None, period_name: str = "",
                    bridge: bool = False, labels: bool = False,
                    accent: str = "", legend: bool = False, months: bool = False,
                    size: tuple[int, int] | None = None, placed_cm: float = 16.5) -> bytes:
    """A PNG of the market price, and the forecast when one is given.

    `history` and `forecast` are (date, value); `band` is (date, low, high) for the range.
    `second` is another series on the same axes - the feedstock under the material - which is how a
    reader sees in one picture that benzene moved four times as far as the pMDI above it.

    Nothing is smoothed, interpolated or invented. Where a market stopped printing, the line stops
    too: see `_breaks`.
    """
    from PIL import Image, ImageDraw

    def clean(rows):
        out = []
        for day, value in rows or []:
            when = _as_date(day)
            if when is None or value is None:
                continue
            out.append((when, float(value)))
        return out

    history, forecast, second = clean(history), clean(forecast), clean(second)
    band = [(d, float(lo), float(hi)) for d, lo, hi
            in ((_as_date(day), low, high) for day, low, high in band or []) if d is not None]
    days = ([d for d, _v in history] + [d for d, _v in forecast] + [d for d, _l, _h in band]
            + [d for d, _v in second])
    values = ([v for _d, v in history] + [v for _d, v in forecast]
              + [x for _d, lo, hi in band for x in (lo, hi)] + [v for _d, v in second])
    width, height = size or (WIDTH, HEIGHT)
    # A chart set two or three to a row is read at a third of the width it used to be drawn at, so
    # the type is sized against the picture rather than fixed. Below 1.0 nothing shrinks: the
    # full-width chart is exactly what it was.
    # Type is sized so that it comes out the same height on the page whatever size the picture is
    # drawn at. A chart placed in a column one third of the width needs type three times as large
    # in the picture to read the same as the one drawn full width, which is why `placed_cm` is
    # part of this and the pixel width alone is not.
    scale = max((width / WIDTH) * (16.5 / max(placed_cm, 1.0)), 0.55)
    image = Image.new("RGB", (width, height), WHITE)
    draw = ImageDraw.Draw(image)
    small, tiny = _font(int(28 * scale)), _font(int(24 * scale))
    # Room for the price labels on the left and the dates underneath, at whatever size they are.
    margin_l = max(int(2.8 * tiny.size + 34), 90)
    margin_r = max(int(0.8 * tiny.size + 14), 30)
    margin_b = max(int(2.4 * tiny.size + 30), 80)
    ahead_line = ORANGE if accent == "orange" else TEAL
    ahead_band = ORANGE_BAND if accent == "orange" else BAND
    ahead_wash = ORANGE_WASH if accent == "orange" else AHEAD
    if not days or not values:
        draw.text((margin_l, height // 2), "No price history for this market yet.",
                  fill=GREY, font=small)
        out = BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()

    legend_rows = [("Historical", BLUE, "line")]
    if legend and forecast:
        legend_rows.append(("Forecast (central case)", ahead_line, "dash"))
    if legend and band:
        legend_rows.append(("Forecast range", ahead_band, "block"))
    top_margin = int(MARGIN_T * scale) + (int(46 * scale) if title else 0) \
        + (int(34 * scale) if legend else 0)
    first, last = min(days), max(days)
    span = max((last - first).days, 1)
    low, high = min(values), max(values)
    pad = (high - low) * 0.10 or max(high * 0.02, 1.0)
    low, high = low - pad, high + pad
    ticks = _nice_ticks(low, high)

    def px(day: date) -> float:
        return margin_l + (day - first).days / span * (width - margin_l - margin_r)

    def py(value: float) -> float:
        return height - margin_b - (value - low) / (high - low) * (height - top_margin - margin_b)

    # The period the report is about, behind everything else.
    if period and period[0] and period[1]:
        from_day, to_day = max(_as_date(period[0]), first), min(_as_date(period[1]), last)
        if from_day and to_day and from_day <= to_day:
            draw.rectangle([px(from_day), top_margin, px(to_day), height - margin_b], fill=PERIOD)
            if period_name:
                draw.text((px(from_day) + 12, top_margin + 8), period_name, fill=GREY, font=tiny)

    # Where the record stops and the forecast starts. Charlie, 10 October 2026: "The historical
    # price development and projected price range must be visually distinguishable. Add a clearly
    # labelled forecast boundary and show the forecast horizon." Nothing about the values changes;
    # the picture says which side of the line a reader is looking at.
    ahead = max([d for d, _v in forecast] + [d for d, _l, _h in band], default=None)
    starts = min([d for d, _v in forecast] + [d for d, _l, _h in band], default=None)
    boundary = max([d for d, _v in history], default=None) if starts else None
    if boundary and ahead and boundary < ahead:
        draw.rectangle([px(boundary), top_margin, px(ahead), height - margin_b], fill=ahead_wash)

    for tick in ticks:
        if tick < low or tick > high:
            continue
        y = py(tick)
        draw.line([(margin_l, y), (width - margin_r, y)], fill=GRID, width=2)
        label = f"{tick:,.0f}"
        draw.text((margin_l - 18 - draw.textlength(label, font=tiny), y - 14), label,
                  fill=GREY, font=tiny)

    # Dates a reader can actually use. Stefan, 11 October 2026: "The Ethylene Oxide, Propylene
    # Oxide and Toluene charts have no sufficiently readable date ticks." They had none at all:
    # the ticks were the first of each month, and a series running 10 to 30 September contains no
    # first of a month. The spacing now comes from how long the picture covers, and the first and
    # last days drawn are always written, so the observation window can be read off the axis
    # without reading the text.
    kept: list[tuple[float, str, float, float]] = []
    for when, label in (_month_ticks(first, last) if months
                        else _date_ticks(first, last, span)):
        x, wide = px(when), draw.textlength(label, font=tiny)
        # Where the label is actually WRITTEN, which is not always centred on its tick: one at
        # the edge of the picture is pulled inwards to stay on it. The touching test used to
        # measure between tick centres, so a last label pulled left could sit on top of the one
        # before it while the test said they were clear. It showed on 23 November 2026 the moment
        # a forecast stopped ending on the 1st of a month: "Feb 27" and "22 Feb 2027", one word.
        left = min(max(x - wide / 2, 8), width - wide - 8)
        # Where two labels would touch, the later one wins, so the last day drawn is always
        # written even when the regular spacing lands one day short of it.
        while kept and left < kept[-1][3] + kept[-1][2] + 16:
            kept.pop()
        kept.append((x, label, wide, left))
    for x, label, wide, left in kept:
        draw.line([(x, top_margin), (x, height - margin_b)], fill=GRID, width=1)
        draw.text((left, height - margin_b + 18), label, fill=GREY, font=tiny)

    draw.line([(margin_l, height - margin_b), (width - margin_r, height - margin_b)],
              fill=GREY, width=2)
    # Both axes say what they are. Stefan, 11 October 2026.
    draw.text((margin_l, int(8 * scale)), f"Price ({units.pair(currency)})", fill=GREY, font=tiny)
    across = "Date"
    draw.text(((margin_l + width - margin_r) / 2 - draw.textlength(across, font=tiny) / 2,
               height - margin_b + int(52 * scale)), across, fill=GREY, font=tiny)
    if title:
        draw.text((margin_l, int(38 * scale)), title, fill=NAVY, font=_font(int(34 * scale)))
    # What each stroke in the picture is, said in the picture. A reader holding one chart out of
    # a row of three should not have to look anywhere else to know which line is the record and
    # which is the forecast.
    if legend:
        at = margin_l
        row = top_margin - int(26 * scale)
        tiniest = _font(max(int(20 * scale), 9))
        swatch, gap = int(30 * scale), int(8 * scale)
        room = width - margin_r - margin_l
        wanted = sum(swatch + gap + draw.textlength(name, font=tiniest) + int(22 * scale)
                     for name, _colour, _kind in legend_rows)
        if wanted > room and len(legend_rows) > 1:
            tiniest = _font(max(int(17 * scale), 8))
        for name, colour, kind in legend_rows:
            if kind == "block":
                draw.rectangle([at, row - int(6 * scale), at + swatch, row + int(10 * scale)],
                               fill=colour)
            elif kind == "dash":
                _dashed(draw, [(at, row + int(2 * scale)), (at + swatch, row + int(2 * scale))],
                        colour, max(int(4 * scale), 2), on=int(9 * scale), off=int(6 * scale))
            else:
                draw.line([(at, row + int(2 * scale)), (at + swatch, row + int(2 * scale))],
                          fill=colour, width=max(int(4 * scale), 2))
            at += swatch + gap
            draw.text((at, row - int(7 * scale)), name, fill=GREY, font=tiniest)
            at += draw.textlength(name, font=tiniest) + int(22 * scale)

    if band:
        top = [(px(d), py(hi)) for d, _lo, hi in band]
        bottom = [(px(d), py(lo)) for d, lo, _hi in reversed(band)]
        if len(top) + len(bottom) >= 3:
            draw.polygon(top + bottom, fill=ahead_band)

    # A dot per reading is detail on a short view and noise on a long one, and the decision is
    # made once for the picture: one line with dots beside one without reads as two kinds of thing.
    marks = max(len(history), len(second)) <= 40

    # `stroke`, not `width`: a parameter called width here shadowed the picture's own width, and
    # the price labels were placed against a boundary of four pixels and drawn off the canvas.
    def draw_series(rows, colour, stroke, label_ends=False):
        """One series, in the stretches where it was actually published."""
        if not rows:
            return
        points = [(px(d), py(v)) for d, v in rows]
        breaks = set() if bridge else _breaks([d for d, _v in rows])
        for run in _runs(points, breaks):
            if len(run) > 1:
                draw.line(run, fill=colour, width=stroke, joint="curve")
            elif run:
                x, y = run[0]
                draw.ellipse([x - 5, y - 5, x + 5, y + 5], fill=colour)
            if marks:
                for x, y in run:
                    draw.ellipse([x - 6, y - 6, x + 6, y + 6], fill=colour)
        if label_ends:
            written.extend(_label_points(draw, rows, px, py, colour, tiny, currency,
                                         bounds=(margin_l, width - margin_r)))

    written: list = []
    draw_series(second, AMBER, 3)
    draw_series(history, BLUE, 4, label_ends=labels)

    if forecast:
        anchor = [(px(history[-1][0]), py(history[-1][1]))] if history else []
        line = anchor + [(px(d), py(v)) for d, v in forecast]
        if len(line) > 1:
            _dashed(draw, line, ahead_line, 4)
        for x, y in line[1:]:
            draw.ellipse([x - 7, y - 7, x + 7, y + 7], fill=ahead_line)

    # Where the range ends, written on the picture. Stefan, 12 October 2026: the upper and the
    # lower limit of the forecast should be data labels. They are the two figures a buyer plans
    # against - the worst the method has covered and the best - and reading them off an axis is
    # not reading them. Both are measured, never drawn wider than the band they label.
    if band and labels:
        edge_day, edge_low, edge_high = band[-1]
        apart = abs(py(edge_low) - py(edge_high)) > tiny.size * 2.2
        ends = [(edge_high, True)] + ([(edge_low, False)] if apart else [])
        for value, above in ends:
            said = f"{value:,.0f}"
            wide = draw.textlength(said, font=tiny)
            at_x = min(max(px(edge_day) - wide / 2, margin_l + 2), width - margin_r - wide - 2)
            at_y = py(value) - (tiny.size + 8) if above else py(value) + 6
            draw.text((at_x, at_y), said, fill=ahead_line, font=tiny)
            written.append((at_x, at_y, wide))

    if boundary and ahead and boundary < ahead and not legend:
        x = px(boundary)
        _dashed(draw, [(x, top_margin), (x, height - margin_b)], ahead_line, 2, on=10, off=8)
        # One label, saying both where the record ends and how far ahead the forecast runs. It
        # sits in the wash on the forecast side, below the row the period name uses and clear of
        # the dates on the axis.
        said = (f"Forecast {starts:%d %b} to {ahead:%d %b %Y}"
                .replace(" 0", " ").replace("Forecast  ", "Forecast "))
        wide = draw.textlength(said, font=tiny)
        # Against the right edge of the forecast wash rather than against the boundary line. A
        # market that closes at its own high writes the closing figure on that boundary, and the
        # two were printed into each other; the far end of the wash is the one place in the
        # picture nothing else uses. The drop is a backstop for a short forecast window, where
        # the right edge is close to the boundary after all.
        at = max(x + 12, width - margin_r - wide - 10)
        down = top_margin + 44
        for _step in range(6):
            if not any(abs(down - y) < 40 and at < x2 + w2 + 14 and x2 < at + wide + 14
                       for x2, y, w2 in written):
                break
            down += 30
        draw.text((at, down), said, fill=ahead_line, font=tiny)

    def window_of(rows) -> str:
        if not rows or len(rows) < 2:
            return ""
        held = (max(d for d, _v in rows) - min(d for d, _v in rows)).days
        if held >= span * 0.5:
            return ""
        opened = f"{min(d for d, _v in rows):%d %b %Y}".lstrip("0")
        closed = f"{max(d for d, _v in rows):%d %b %Y}".lstrip("0")
        return f" ({opened} to {closed})"

    # A legend only where there is more than one thing to tell apart.
    names = ([(f"{first_name or 'Market price'}{window_of(history)}", BLUE)]
             if (second and first_name or second) else [])
    if second and second_name:
        names.append((f"{second_name}{window_of(second)}", AMBER))
    if forecast:
        names.append(("Forecast", ahead_line))
    if len(names) > 1:
        x = margin_l
        y = height - 44
        for name, colour in names:
            draw.line([(x, y + 12), (x + 40, y + 12)], fill=colour, width=5)
            draw.text((x + 50, y), name, fill=GREY, font=tiny)
            x += 60 + draw.textlength(name, font=tiny) + 40

    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


ROUTE_WIDTH, ROUTE_ROW = 1650, 150


def route_png(steps, *, title: str = "", note: str = "") -> bytes:
    """The making of one material, drawn left to right.

    Stefan, 9 October 2026, asked for the chemical route of the chosen chemicals in the report. A
    buyer who can see that his pMDI is four steps down from benzene, and that we price three of
    those steps, understands in one picture why the benzene line on the chart above matters.

    `steps` is (name, priced) in making order. A step we price is filled; one we do not is outlined,
    because a reader must be able to tell a market from a reaction.
    """
    from PIL import Image, ImageDraw

    steps = [(str(name), bool(priced)) for name, priced in steps or []]
    if not steps:
        return b""
    per_row = min(len(steps), 8)
    rows = [steps[i:i + per_row] for i in range(0, len(steps), per_row)]
    height = 70 + len(rows) * ROUTE_ROW + (40 if note else 0)
    image = Image.new("RGB", (ROUTE_WIDTH, height), WHITE)
    draw = ImageDraw.Draw(image)
    label_font, title_font, note_font = _font(26), _font(34), _font(24)

    top = 10
    if title:
        draw.text((40, top), title, fill=NAVY, font=title_font)
        top += 54

    gap = 60 if per_row <= 5 else 40
    box_w = (ROUTE_WIDTH - 80 - (per_row - 1) * gap) / per_row
    for index, row in enumerate(rows):
        y = top + index * ROUTE_ROW
        for position, (name, priced) in enumerate(row):
            x = 40 + position * (box_w + gap)
            box = [x, y, x + box_w, y + 86]
            if priced:
                draw.rounded_rectangle(box, radius=10, fill=NAVY)
                colour = WHITE
            else:
                draw.rounded_rectangle(box, radius=10, outline=GREY, width=3)
                colour = GREY
            words, line, lines = name.split(), "", []
            for word in words:
                trial = (line + " " + word).strip()
                if draw.textlength(trial, font=label_font) > box_w - 24 and line:
                    lines.append(line)
                    line = word
                else:
                    line = trial
            lines.append(line)
            lines = lines[:2]
            start_y = y + 43 - len(lines) * 17
            for offset, text in enumerate(lines):
                draw.text((x + box_w / 2 - draw.textlength(text, font=label_font) / 2,
                           start_y + offset * 34), text, fill=colour, font=label_font)
            last = position == len(row) - 1
            if not last:
                mid = y + 43
                draw.line([(x + box_w + 10, mid), (x + box_w + 44, mid)], fill=GREY, width=4)
                draw.polygon([(x + box_w + 50, mid), (x + box_w + 36, mid - 9),
                              (x + box_w + 36, mid + 9)], fill=GREY)
            elif index + 1 < len(rows):
                # Carries on below: a hook down the right and back to the left of the next row.
                mid = y + 43
                draw.line([(x + box_w + 10, mid), (ROUTE_WIDTH - 20, mid),
                           (ROUTE_WIDTH - 20, y + ROUTE_ROW - 20), (20, y + ROUTE_ROW - 20),
                           (20, y + ROUTE_ROW + 43)], fill=GREY, width=3)
                draw.polygon([(34, y + ROUTE_ROW + 43), (20, y + ROUTE_ROW + 30),
                              (20, y + ROUTE_ROW + 56)], fill=GREY)

    if note:
        draw.text((40, height - 34), note, fill=GREY, font=note_font)
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()
