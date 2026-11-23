"""The production route, drawn.

The chemical relationships are not a hypothesis - toluene is nitrated to DNT, DNT is hydrogenated to
TDA, TDA is phosgenated to TDI. That is established chemistry, and a reader should be able to see it
without being told about correlation coefficients first.

This turns a slice of the governed route graph into Graphviz DOT, which Streamlit renders in the
browser. Three things are encoded visually, because they are the three things a reader needs:

- **the raw material** is the one filled dark node, so the eye finds it immediately;
- **chemicals with a market price** are solid, and chemicals nobody quotes are dashed and grey -
  the difference between a link we can measure and a link we can only assert;
- **where the chain starts** - crude oil, brine, air - is amber, so the origin of the route is
  visible rather than buried at the left-hand edge.

Line style alone turned out not to carry any of that. A reader looking at twelve boxes cannot tell a
dashed border from a solid one, and nothing on the picture said what either meant - so the count in
the caption and the count in the tables below looked like a contradiction rather than two different
populations. Every box now states its own status in words, and a legend sits in the corner.
"""
from __future__ import annotations

from io import BytesIO

from .chemical_routes import ChemicalRouteGraph, RouteEdge

# Colours are fixed rather than themed: a diagram that renders as an image has to be legible on the
# light background Streamlit gives it, and mid-tones on white are what achieve that.
TARGET_FILL = "#1e293b"
TARGET_TEXT = "#f8fafc"
PRICED_FILL = "#ffffff"
PRICED_LINE = "#475569"
UNPRICED_FILL = "#f1f5f9"
UNPRICED_LINE = "#94a3b8"
TESTED_FILL = "#e0f2fe"
TESTED_LINE = "#0369a1"
ROOT_FILL = "#fef3c7"
ROOT_LINE = "#b45309"
EDGE_LINE = "#94a3b8"
TEXT = "#0f172a"

RELATION_WORDS = {
    "FEEDSTOCK_OF": "feedstock",
    "INTERMEDIATE_OF": "intermediate",
    "STARTER_FOR": "starter",
    "PROCESS_REAGENT_FOR": "reagent",
    "CATALYST_FOR": "catalyst",
    "CO_PRODUCT_OF": "co-product",
    "SEPARATED_TO": "separated",
    "REFINED_TO": "refined",
}


def _escape(value: str) -> str:
    return str(value).replace('"', '\\"')


def _tooltip(node) -> str:
    parts = [node.full_name or node.display_name]
    if node.cas:
        parts.append(f"CAS {node.cas}")
    if not node.price_tracked:
        parts.append("no market price published")
    return " · ".join(parts)


def route_dot(graph: ChemicalRouteGraph, target: str, *, max_chemical_steps: int = 4,
              route_ids: list[str] | None = None, show_relations: bool = False,
              tested: dict[str, int] | None = None, legend: bool = True) -> str:
    """DOT for the production routes into `target`. Empty graph if the chemical is unknown.

    `tested` maps a chemical code to how many priced steps it sits from the target - exactly the set
    the correlation work is using at the current setting. Passing it is what lets the picture and the
    tables below agree instead of appearing to contradict each other.
    """
    codes, edges = graph.upstream_network(target, max_chemical_steps=max_chemical_steps,
                                          route_ids=route_ids)
    if not codes:
        return 'digraph routes { label="" }'

    lines = [
        "digraph routes {",
        '  rankdir=LR;',
        '  bgcolor="transparent";',
        '  splines=spline;',
        '  nodesep=0.28;',
        '  ranksep=0.85;',
        '  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11,'
        f' penwidth=1.1, margin="0.16,0.09", color="{PRICED_LINE}", fontcolor="{TEXT}"];',
        f'  edge [color="{EDGE_LINE}", penwidth=1.1, arrowsize=0.65,'
        ' fontname="Helvetica", fontsize=9, fontcolor="#64748b"];',
    ]

    tested = dict(tested or {})
    for code in codes:
        node = graph.node(code)
        if node is None:
            continue
        # The second line of the label is the whole point: a reader should not have to decode a
        # border style to learn whether a chemical is in the measurement or only in the chemistry.
        if code == target:
            fill, line, text = TARGET_FILL, TARGET_FILL, TARGET_TEXT
            status = "the raw material"
        elif not node.price_tracked:
            fill, line, text = UNPRICED_FILL, UNPRICED_LINE, "#475569"
            status = "no market price"
        elif code in tested:
            steps = tested[code]
            fill, line, text = TESTED_FILL, TESTED_LINE, TEXT
            status = f"measured · {steps} step" + ("" if steps == 1 else "s")
        else:
            fill, line, text = (ROOT_FILL, ROOT_LINE, TEXT) if node.is_root else (PRICED_FILL, PRICED_LINE, TEXT)
            status = "priced · outside the step setting"
        style = "rounded,filled" if node.price_tracked else "rounded,filled,dashed"
        label = f"{_escape(node.display_name)}\\n{_escape(status)}"
        lines.append(
            f'  "{_escape(code)}" [label="{label}", fillcolor="{fill}",'
            f' color="{line}", fontcolor="{text}", style="{style}",'
            f' tooltip="{_escape(_tooltip(node))}"];'
        )

    # Propylene makes PO five different ways, and drawing five parallel arrows between the same two
    # boxes says nothing the one arrow does not. The routes are merged for the picture and listed in
    # the arrow's tooltip; the list view below the diagram keeps every route separately.
    merged: dict[tuple[str, str], list[RouteEdge]] = {}
    for edge in edges:
        merged.setdefault((edge.from_chemical, edge.to_chemical), []).append(edge)

    for (source, product), group in merged.items():
        routes = sorted({edge.route_id for edge in group})
        relations = {edge.relation for edge in group}
        attrs = [f'tooltip="{_escape(", ".join(routes))}"']
        if show_relations and len(relations) == 1:
            word = RELATION_WORDS.get(next(iter(relations)), next(iter(relations)).lower())
            attrs.append(f'label="{_escape(word)}"')
        if relations == {"CO_PRODUCT_OF"}:
            attrs.append('style="dashed"')
        if len(routes) > 1:
            # More than one production route runs along this arrow; a thicker line says so without
            # adding a second arrow.
            attrs.append("penwidth=2.0")
        lines.append(f'  "{_escape(source)}" -> "{_escape(product)}" [{", ".join(attrs)}];')

    if legend:
        lines.extend(_legend())
    lines.append("}")
    return "\n".join(lines)


def _legend() -> list[str]:
    """A key, drawn as its own cluster so it cannot be mistaken for part of the chain."""
    return [
        '  subgraph cluster_legend {',
        '    label="What the boxes mean";',
        '    labeljust="l"; fontname="Helvetica"; fontsize=10; fontcolor="#475569";',
        f'    color="{UNPRICED_LINE}"; style="rounded"; margin=10;',
        '    node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=9,'
        '          margin="0.12,0.06", penwidth=1.1];',
        f'    legend_target [label="the raw material", fillcolor="{TARGET_FILL}",'
        f' color="{TARGET_FILL}", fontcolor="{TARGET_TEXT}"];',
        f'    legend_tested [label="measured against it", fillcolor="{TESTED_FILL}",'
        f' color="{TESTED_LINE}", fontcolor="{TEXT}"];',
        f'    legend_priced [label="priced, outside the step setting", fillcolor="{PRICED_FILL}",'
        f' color="{PRICED_LINE}", fontcolor="{TEXT}"];',
        f'    legend_unpriced [label="no market price — never measurable", fillcolor="{UNPRICED_FILL}",'
        f' color="{UNPRICED_LINE}", fontcolor="#475569", style="rounded,filled,dashed"];',
        '    legend_target -> legend_tested -> legend_priced -> legend_unpriced'
        ' [style=invis];',
        '  }',
    ]


def route_summary(graph: ChemicalRouteGraph, target: str, *, max_chemical_steps: int = 4) -> dict:
    """The counts a caption needs, so the caption does not have to guess."""
    codes, edges = graph.upstream_network(target, max_chemical_steps=max_chemical_steps)
    upstream = [code for code in codes if code != target]
    priced = [code for code in upstream if (graph.node(code) or None) and graph.node(code).price_tracked]
    routes = sorted({edge.route_id for edge in edges})
    roots = sorted({code for code in upstream if graph.node(code) and graph.node(code).is_root})
    return {
        "chemicals": len(upstream),
        "priced": len(priced),
        "unpriced": len(upstream) - len(priced),
        "routes": routes,
        "roots": [graph.node(code).display_name for code in roots],
    }


def route_chemicals_table(graph: ChemicalRouteGraph, target: str, *,
                          max_chemical_steps: int = 4) -> list[dict]:
    """The route as one row per chemical, which is the unit the rest of the page counts.

    The edge list below this was the fourth different population on one screen: 29 chemicals in the
    caption, 18 process segments, 43 edges here, 21 rows in the table under it. Four numbers, no
    stated relation between any two of them, and a reader left to assume the page was contradicting
    itself. One row per chemical makes this list and the caption the same count, and the process and
    the route become columns on the chemical rather than populations of their own.
    """
    codes, edges = graph.upstream_network(target, max_chemical_steps=max_chemical_steps)
    feeds: dict[str, list[str]] = {}
    made_by: dict[str, list[str]] = {}
    routes: dict[str, set[str]] = {}
    for edge in edges:
        node = graph.node(edge.to_chemical)
        feeds.setdefault(edge.from_chemical, []).append(
            node.display_name if node else edge.to_chemical)
        made_by.setdefault(edge.to_chemical, []).append(
            edge.technical_qualifier or RELATION_WORDS.get(edge.relation, edge.relation.lower()))
        routes.setdefault(edge.from_chemical, set()).add(edge.route_id)

    rows = []
    for code in sorted(c for c in codes if c != target):
        node = graph.node(code)
        rows.append({
            "Chemical": node.label if node else code.replace("_", " "),
            "CAS": (node.cas if node else "") or "—",
            "Goes into": ", ".join(sorted(set(feeds.get(code, [])))) or "—",
            "How it is made": ", ".join(sorted(set(made_by.get(code, [])))) or "starting material",
            "Market price": ("Quoted" if (node and node.price_tracked) else
                             "Not quoted - made and used inside the plant"),
            "Route": ", ".join(sorted(routes.get(code, set()))) or "—",
        })
    return rows


def route_table(graph: ChemicalRouteGraph, target: str, *, max_chemical_steps: int = 4,
                route_ids: list[str] | None = None) -> list[dict]:
    """The same relationships as a list, for readers who want the text rather than the picture."""
    _codes, edges = graph.upstream_network(target, max_chemical_steps=max_chemical_steps,
                                           route_ids=route_ids)
    rows = []
    for edge in sorted(edges, key=lambda e: (e.to_chemical, e.from_chemical, e.route_id)):
        source, product = graph.node(edge.from_chemical), graph.node(edge.to_chemical)
        rows.append({
            "Input": source.label if source else edge.from_chemical,
            "Role": RELATION_WORDS.get(edge.relation, edge.relation.lower()),
            "Makes": product.label if product else edge.to_chemical,
            "Process": edge.technical_qualifier or "—",
            "Route": edge.route_id,
        })
    return rows


# The same picture the Relationships screen draws, rendered for the document.
#
# Stefan, 10 October 2026: "And why not use the existing image??" The report had been carrying a
# flattened strip of seven boxes while the application already drew the real chain - twelve
# chemicals, branching, and every one of them saying whether it has a market price. There was no
# reason for two pictures of the same thing, and the screen's is the better one.
#
# The wording differs from the screen's in one way. On screen a chemical reads "priced · outside
# the step setting", which belongs to the correlation work; in a customer's document it says
# whether this edition carries a price for it, and nothing about settings.
REPORT_HERE_FILL = "#e0f2fe"
REPORT_HERE_LINE = "#0369a1"


def report_dot(graph: ChemicalRouteGraph, target: str, *, priced_here: set[str] | None = None,
               max_chemical_steps: int = 6, legend: bool = True) -> str:
    """DOT for the chain into `target`, worded for a customer."""
    here = {str(code) for code in (priced_here or set())}
    codes, edges = graph.upstream_network(target, max_chemical_steps=max_chemical_steps)
    if not codes:
        return 'digraph routes { label="" }'
    lines = [
        "digraph routes {",
        "  rankdir=LR;",
        '  bgcolor="white";',
        "  splines=spline;",
        "  nodesep=0.30;",
        "  ranksep=0.85;",
        '  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11,'
        f' penwidth=1.1, margin="0.16,0.09", color="{PRICED_LINE}", fontcolor="{TEXT}"];',
        f'  edge [color="{EDGE_LINE}", penwidth=1.1, arrowsize=0.65];',
    ]
    for code in codes:
        node = graph.node(code)
        if node is None:
            continue
        if code == target:
            fill, line, text, status = TARGET_FILL, TARGET_FILL, TARGET_TEXT, "the material"
        elif code in here:
            fill, line, text, status = (REPORT_HERE_FILL, REPORT_HERE_LINE, TEXT,
                                        "priced in this report")
        elif not node.price_tracked:
            fill, line, text, status = UNPRICED_FILL, UNPRICED_LINE, "#475569", "no market price"
        else:
            fill, line, text, status = ((ROOT_FILL, ROOT_LINE, TEXT, "where the chain starts")
                                        if node.is_root
                                        else (PRICED_FILL, PRICED_LINE, TEXT, "has a market price"))
        style = "rounded,filled" if node.price_tracked else "rounded,filled,dashed"
        label = f"{_escape(node.display_name)}\\n{_escape(status)}"
        lines.append(f'  "{_escape(code)}" [label="{label}", fillcolor="{fill}",'
                     f' color="{line}", fontcolor="{text}", style="{style}"];')
    drawn = set()
    for edge in edges:
        pair = (edge.from_chemical, edge.to_chemical)
        if pair in drawn:
            continue
        drawn.add(pair)
        lines.append(f'  "{_escape(pair[0])}" -> "{_escape(pair[1])}";')
    if legend:
        lines.extend(_report_legend())
    lines.append("}")
    return "\n".join(lines)


def _report_legend() -> list[str]:
    return [
        "  subgraph cluster_legend {",
        '    label="What the boxes mean";',
        '    fontname="Helvetica"; fontsize=10; fontcolor="#475569";',
        '    color="#cbd5e1"; style="rounded"; margin=10;',
        f'    legend_target [label="the material", fillcolor="{TARGET_FILL}",'
        f' color="{TARGET_FILL}", fontcolor="{TARGET_TEXT}"];',
        f'    legend_here [label="priced in this report", fillcolor="{REPORT_HERE_FILL}",'
        f' color="{REPORT_HERE_LINE}"];',
        f'    legend_priced [label="has a market price", fillcolor="{PRICED_FILL}",'
        f' color="{PRICED_LINE}"];',
        f'    legend_root [label="where the chain starts", fillcolor="{ROOT_FILL}",'
        f' color="{ROOT_LINE}"];',
        f'    legend_unpriced [label="no market price", fillcolor="{UNPRICED_FILL}",'
        f' color="{UNPRICED_LINE}", fontcolor="#475569", style="rounded,filled,dashed"];',
        "    legend_target -> legend_here [style=invis];",
        "    legend_here -> legend_priced [style=invis];",
        "    legend_priced -> legend_root [style=invis];",
        "    legend_root -> legend_unpriced [style=invis];",
        "  }",
    ]


def report_png(graph: ChemicalRouteGraph, target: str, *, priced_here: set[str] | None = None,
               max_chemical_steps: int = 6, width_px: int = 1650) -> bytes:
    """The chain as a picture. Empty where Graphviz is not installed, so the caller can fall back."""
    import shutil
    import subprocess

    dot = shutil.which("dot")
    if not dot:
        return b""
    source = report_dot(graph, target, priced_here=priced_here,
                        max_chemical_steps=max_chemical_steps)
    try:
        done = subprocess.run([dot, "-Tpng", f"-Gdpi=150", f"-Gsize={width_px / 150:.2f},12"],
                              input=source.encode("utf-8"), capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return b""
    return done.stdout if done.returncode == 0 and done.stdout[:4] == b"\x89PNG" else b""


# The same chain again, drawn without Graphviz.
#
# Stefan, 10 October 2026: "the chemical relationship image is still the old one." It was. The
# screen hands its DOT to Streamlit, which renders it in the browser, so the screen never needs
# Graphviz installed - and the document, which asks `dot` to draw the same source, falls back to
# the flattened strip on a machine that has no `dot`. Nobody is going to install Graphviz on a
# customer's laptop to print a report, so the picture is drawn here with Pillow, which the
# application already has. The content is the screen's: every chemical on the route, branching,
# each one saying whether this edition carries a price for it.
PICTURE_WIDTH = 1650
BOX_H, ROW_GAP, COL_GAP = 96, 46, 60


def _rgb(value: str) -> tuple[int, int, int]:
    held = str(value).lstrip("#")
    return tuple(int(held[i:i + 2], 16) for i in (0, 2, 4))


def _depths(codes: list[str], edges, target: str) -> dict[str, int]:
    """How many reactions each chemical stands above the material, by the longest path to it."""
    out = {code: 0 for code in codes}
    after: dict[str, list[str]] = {}
    for edge in edges:
        after.setdefault(edge.from_chemical, []).append(edge.to_chemical)
    for _pass in range(len(codes) + 1):
        moved = False
        for code in codes:
            for downstream in after.get(code, ()):
                if downstream in out and out[code] < out[downstream] + 1:
                    out[code] = out[downstream] + 1
                    moved = True
        if not moved:
            break
    out[target] = 0
    return out


def _wrapped(draw, text: str, font, width: float, lines: int = 2) -> list[str]:
    out, line = [], ""
    for word in str(text).split():
        trial = (line + " " + word).strip()
        if line and draw.textlength(trial, font=font) > width:
            out.append(line)
            line = word
        else:
            line = trial
    out.append(line)
    return out[:lines]


# A name the box cannot hold used to be printed straight through its own border: Dimethylolbutanal,
# Formaldehyde, n-Butyraldehyde and Propylene chlorohydrin all ran over the edge, because wrapping
# breaks between words and those names have no space to break at. The status line under the name
# already shrank to fit; the name does the same, and a single word that still will not fit is
# broken with a hyphen rather than allowed outside the box.
NAME_SIZES = (25, 23, 21, 19, 17, 15)


def _broken(draw, word: str, font, width: float) -> list[str] | None:
    """One long word in two, split as near the middle as both halves allow.

    Near the middle rather than as late as it fits: "Dimethylol-butanal" reads as a chemical name
    cut in half, where "Dimethylolbut-anal" reads as a mistake.
    """
    word = str(word)
    middle = len(word) // 2
    for step in range(0, len(word)):
        for cut in {middle - step, middle + step}:
            if cut < 2 or cut > len(word) - 2:
                continue
            head, tail = word[:cut] + "-", word[cut:]
            if (draw.textlength(head, font=font) <= width
                    and draw.textlength(tail, font=font) <= width):
                return [head, tail]
    return None


def _fitted_name(draw, text: str, width: float, lines: int = 2):
    """The name, the font it fits in, and the size it was set at.

    Names used to be drawn at one fixed size and printed straight through the border of any box
    too narrow for them: Dimethylolbutanal, Formaldehyde, n-Butyraldehyde and Propylene
    chlorohydrin all ran over the edge of their boxes. The status line under the name already
    shrank to fit, and now the name does too.

    Whole first, at every size, before anything is broken. Breaking is reserved for a single word
    that no size will hold, so Propylene is set small rather than cut in two.
    """
    from .page_chart import _font

    for size in NAME_SIZES:
        font = _font(size)
        said = _wrapped(draw, text, font, width, lines=lines)
        if all(draw.textlength(one, font=font) <= width for one in said):
            return said, font, size
    words = str(text).split()
    if len(words) == 1:
        for size in NAME_SIZES:
            font = _font(size)
            said = _broken(draw, words[0], font, width)
            if said:
                return said, font, size
    font = _font(NAME_SIZES[-1])
    return _wrapped(draw, text, font, width, lines), font, NAME_SIZES[-1]


def report_picture(graph: ChemicalRouteGraph, target: str, *, priced_here: set[str] | None = None,
                   max_chemical_steps: int = 6, title: str = "") -> bytes:
    """The branching route into `target` as a PNG, worded for a customer."""
    from PIL import Image, ImageDraw

    from .page_chart import GREY, WHITE, _font

    here = {str(code) for code in (priced_here or set())}
    codes, edges = graph.upstream_network(target, max_chemical_steps=max_chemical_steps)
    codes = [code for code in codes if graph.node(code) is not None]
    if not codes:
        return b""
    depth = _depths(codes, edges, target)
    columns: dict[int, list[str]] = {}
    for code in sorted(codes, key=lambda one: (depth[one], str(graph.node(one).display_name))):
        columns.setdefault(depth[code], []).append(code)

    across = max(columns) + 1
    down = max(len(one) for one in columns.values())
    box_w = (PICTURE_WIDTH - 80 - (across - 1) * COL_GAP) / across
    top = 16 + (54 if title else 0)
    height = int(top + down * (BOX_H + ROW_GAP) + 96)
    image = Image.new("RGB", (PICTURE_WIDTH, height), WHITE)
    draw = ImageDraw.Draw(image)
    name_font, note_font, title_font = _font(25), _font(20), _font(34)
    if title:
        draw.text((40, 16), title, fill=_rgb(TEXT), font=title_font)

    def place(code: str) -> tuple[float, float]:
        column = columns[depth[code]]
        x = 40 + (across - 1 - depth[code]) * (box_w + COL_GAP)
        middle = top + (down * (BOX_H + ROW_GAP) - len(column) * (BOX_H + ROW_GAP)) / 2
        y = middle + column.index(code) * (BOX_H + ROW_GAP)
        return x, y

    # The arrows first, so a box always sits on top of the line that reaches it.
    drawn = set()
    for edge in edges:
        pair = (edge.from_chemical, edge.to_chemical)
        if pair in drawn or pair[0] not in depth or pair[1] not in depth:
            continue
        drawn.add(pair)
        x1, y1 = place(pair[0])
        x2, y2 = place(pair[1])
        start = (x1 + box_w + 6, y1 + BOX_H / 2)
        end = (x2 - 16, y2 + BOX_H / 2)
        if end[0] <= start[0]:
            continue
        bend = (max(start[0] + 10, end[0] - COL_GAP * 0.6), start[1])
        draw.line([start, bend, (bend[0], end[1]), end], fill=_rgb(EDGE_LINE), width=3)
        draw.polygon([(end[0] + 12, end[1]), (end[0] - 2, end[1] - 8), (end[0] - 2, end[1] + 8)],
                     fill=_rgb(EDGE_LINE))

    for code in codes:
        node = graph.node(code)
        if code == target:
            fill, line, text, status = TARGET_FILL, TARGET_FILL, TARGET_TEXT, "the material"
        elif code in here:
            fill, line, text, status = (REPORT_HERE_FILL, REPORT_HERE_LINE, TEXT,
                                        "priced in this report")
        elif not node.price_tracked:
            fill, line, text, status = UNPRICED_FILL, UNPRICED_LINE, "#475569", "no market price"
        elif node.is_root:
            fill, line, text, status = ROOT_FILL, ROOT_LINE, TEXT, "where the chain starts"
        else:
            fill, line, text, status = PRICED_FILL, PRICED_LINE, TEXT, "has a market price"
        x, y = place(code)
        draw.rounded_rectangle([x, y, x + box_w, y + BOX_H], radius=12, fill=_rgb(fill),
                               outline=_rgb(line), width=3)
        said, name_font, name_size = _fitted_name(draw, node.display_name, box_w - 24)
        step = name_size + 5
        # The status has to sit inside its box. A long one is set smaller, and one that will not
        # fit on a line at any size is wrapped onto two rather than printed over the border:
        # "where the chain starts" is twenty-two characters and the narrow boxes are not.
        fitted, status_lines = note_font, [status]
        for size in (20, 18, 16, 14, 12):
            fitted = _font(size)
            if draw.textlength(status, font=fitted) <= box_w - 20:
                break
        else:
            fitted = _font(14)
            status_lines = _wrapped(draw, status, fitted, box_w - 20, lines=2)
        note_step = fitted.size + 3
        block = len(said) * step + 4 + len(status_lines) * note_step
        at = y + (BOX_H - block) / 2
        for index, one in enumerate(said):
            draw.text((x + box_w / 2 - draw.textlength(one, font=name_font) / 2, at + index * step),
                      one, fill=_rgb(text), font=name_font)
        under = at + len(said) * step + 4
        for index, one in enumerate(status_lines):
            draw.text((x + box_w / 2 - draw.textlength(one, font=fitted) / 2,
                       under + index * note_step), one, fill=_rgb(text), font=fitted)

    # What the boxes mean, along the foot.
    legend = [("the material", TARGET_FILL, TARGET_FILL),
              ("priced in this report", REPORT_HERE_FILL, REPORT_HERE_LINE),
              ("has a market price", PRICED_FILL, PRICED_LINE),
              ("where the chain starts", ROOT_FILL, ROOT_LINE),
              ("no market price", UNPRICED_FILL, UNPRICED_LINE)]
    x = 40
    y = height - 54
    for words, fill, line in legend:
        draw.rounded_rectangle([x, y, x + 34, y + 26], radius=6, fill=_rgb(fill),
                               outline=_rgb(line), width=2)
        draw.text((x + 46, y + 2), words, fill=GREY, font=note_font)
        x += 46 + draw.textlength(words, font=note_font) + 46
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()
