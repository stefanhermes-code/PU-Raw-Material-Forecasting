"""The governed chemical production graph, and the forecast candidates it generates.

Two jobs, deliberately separate.

**What is chemically true.** Every edge states a direct process relationship: this chemical is
consumed in making that one, in this named production route. Nothing commercial lives here - not
substitution, not co-purchasing, not price materiality. The acceptance test is the one from the
technical model: if a chemical engineer reads a row with no market context, is the stated direct
process relationship true? An edge that only holds because of a skipped intermediate does not belong.

**Which chemicals could predict a price.** The reason to hold the graph at all is to constrain the
search for correlations. With seventy chemicals and a choice of lags, testing everything against TDI
will find impressive relationships that are pure noise. The graph is the prior: only what is
chemically upstream of a raw material is worth testing as a predictor of it.

Distance is measured in PRICE-TRACKED nodes, not chemical steps. Chlorine reaches TDI through
phosgene, which no one quotes a price for, so in price-transmission terms chlorine is one step from
TDI even though it is two reactions away. That is what makes the traversal useful to forecasting.

What this module does NOT do is decide that a candidate is informative. Toluene is one priced step
from TDI and its price is set mostly by gasoline and BTX economics; aniline is one priced step from
MDI and MDI is the dominant consumer of aniline. Same distance, entirely different signal. Which
candidates actually carry information is a question for evidence, not for adjacency.
"""
from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# ----------------------------------------------------------------------------- vocabulary

FEEDSTOCK_OF = "FEEDSTOCK_OF"
INTERMEDIATE_OF = "INTERMEDIATE_OF"
STARTER_FOR = "STARTER_FOR"
PROCESS_REAGENT_FOR = "PROCESS_REAGENT_FOR"
CATALYST_FOR = "CATALYST_FOR"
CO_PRODUCT_OF = "CO_PRODUCT_OF"
SEPARATED_TO = "SEPARATED_TO"
REFINED_TO = "REFINED_TO"

RELATIONS: frozenset[str] = frozenset({
    FEEDSTOCK_OF, INTERMEDIATE_OF, STARTER_FOR, PROCESS_REAGENT_FOR,
    CATALYST_FOR, CO_PRODUCT_OF, SEPARATED_TO, REFINED_TO,
})

# Symmetric relations are stored once and read both ways, so the mirror cannot drift out of step
# with its twin.
SYMMETRIC: frozenset[str] = frozenset({CO_PRODUCT_OF})

# Relations that mean "the source is consumed to make the target". These are the ones the upstream
# traversal follows: a catalyst or a co-product is not a cost input flowing into the target.
PRODUCING: frozenset[str] = frozenset({
    FEEDSTOCK_OF, INTERMEDIATE_OF, STARTER_FOR, PROCESS_REAGENT_FOR, SEPARATED_TO, REFINED_TO,
})

# Where a chain terminates when it stops being petroleum. A brine-rooted chain and a crude-rooted
# chain behave nothing alike, which is why the root is recorded rather than assumed.
ROOT_CLASSES: frozenset[str] = frozenset({
    "CRUDE", "NATURAL_GAS", "BRINE", "AIR", "WATER", "BIOMASS", "MIXED", "REVIEW",
})


@dataclass(frozen=True)
class ChemicalNode:
    code: str
    display_name: str
    price_tracked: bool = True
    root_class: str | None = None
    is_root: bool = False
    aliases: tuple[str, ...] = ()
    note: str = ""
    active: bool = True
    # `display_name` is what the trade calls it - often an abbreviation. `full_name` is the chemical
    # written out, when that is a different string. `cas` removes the last of the ambiguity: TDI is
    # 26471-62-5 whatever anyone calls it. Streams and polymers have no single CAS and carry none
    # rather than a plausible-looking one.
    full_name: str = ""
    cas: str = ""

    @property
    def label(self) -> str:
        """Abbreviation and full name together, which is how a chemical should be named on screen."""
        if self.full_name and self.full_name.lower() != self.display_name.lower():
            return f"{self.display_name} ({self.full_name})"
        return self.display_name


@dataclass(frozen=True)
class RouteEdge:
    route_id: str
    from_chemical: str
    relation: str
    to_chemical: str
    technical_qualifier: str = ""
    active: bool = True
    note: str = ""

    @property
    def key(self) -> tuple[str, str, str, str]:
        """Identity of an edge. The same from/to pair may be valid in another technical route."""
        return (self.route_id, self.from_chemical, self.relation, self.to_chemical)


@dataclass(frozen=True)
class Candidate:
    """One chemical that could carry price information about a raw material."""
    chemical: str
    priced_steps: int          # price-tracked nodes between it and the target, not reactions
    chemical_steps: int
    route_ids: tuple[str, ...]
    path: tuple[str, ...]      # the chemical path, including unpriced intermediates
    root_class: str | None


# ----------------------------------------------------------------------------- validation

@dataclass(frozen=True)
class PricedLink:
    """One step of the chain between two chemicals somebody quotes.

    `via` names the captive intermediates crossed on the way - phosgene, DNT, TDA. They carry no
    price and so cannot be measured, but naming them is what makes the step legible as chemistry
    rather than as an arbitrary pairing.
    """
    from_chemical: str
    to_chemical: str
    via: tuple[str, ...]
    route_ids: tuple[str, ...]
    priced_steps_to_target: int
    paths: int = 1                 # how many distinct ways this step can be made


class RouteValidationError(ValueError):
    pass


_CAS_PATTERN = re.compile(r"^(\d{2,7})-(\d{2})-(\d)$")


def valid_cas(value: str) -> bool:
    """Check a CAS Registry Number against its own check digit.

    The last digit is a weighted sum of the others, so a transposed or mistyped number fails here.
    It cannot tell whether the number is the RIGHT substance - only that it is a real, well-formed
    CAS number - so this catches transcription errors, not wrong attributions.
    """
    match = _CAS_PATTERN.match(str(value).strip())
    if not match:
        return False
    body = match.group(1) + match.group(2)
    checksum = sum((index + 1) * int(digit) for index, digit in enumerate(reversed(body)))
    return checksum % 10 == int(match.group(3))


def validate(nodes: Iterable[ChemicalNode], edges: Iterable[RouteEdge]) -> list[str]:
    """Every rule from section 7 of the technical model. Returns the problems; empty means valid."""
    nodes = list(nodes)
    edges = list(edges)
    codes = {node.code for node in nodes}
    problems: list[str] = []

    duplicate_codes = {code for code in codes if [n.code for n in nodes].count(code) > 1}
    for code in sorted(duplicate_codes):
        problems.append(f"chemical {code} is defined more than once")

    for node in nodes:
        if node.cas and not valid_cas(node.cas):
            problems.append(f"chemical {node.code} has an invalid CAS number {node.cas}")
        if node.root_class and node.root_class not in ROOT_CLASSES:
            problems.append(f"chemical {node.code} has unknown root class {node.root_class}")
        if node.root_class and not node.is_root:
            problems.append(f"chemical {node.code} carries a root class but is not a root")

    seen: set[tuple[str, str, str, str]] = set()
    for edge in edges:
        if edge.relation not in RELATIONS:
            problems.append(f"{edge.route_id}: unknown relation {edge.relation}")
            continue
        if edge.from_chemical == edge.to_chemical:
            problems.append(f"{edge.route_id}: self-edge on {edge.from_chemical}")
        for code in (edge.from_chemical, edge.to_chemical):
            if code not in codes:
                problems.append(f"{edge.route_id}: {code} is not a governed chemical")
        # Duplicates are rejected only within the same route: the same pair may be valid elsewhere.
        if edge.key in seen:
            problems.append(f"{edge.route_id}: duplicate edge {edge.from_chemical} -> {edge.to_chemical}")
        seen.add(edge.key)
        if edge.relation in SYMMETRIC:
            mirror = (edge.route_id, edge.to_chemical, edge.relation, edge.from_chemical)
            if mirror in seen and mirror != edge.key:
                problems.append(
                    f"{edge.route_id}: {edge.relation} stored in both directions for "
                    f"{edge.from_chemical}/{edge.to_chemical}; store it once")

    # A direct edge may not lean on an intermediate that exists only in the qualifier text.
    for edge in edges:
        qualifier = (edge.technical_qualifier or "").lower()
        if " via " in f" {qualifier} ":
            problems.append(
                f"{edge.route_id}: {edge.from_chemical} -> {edge.to_chemical} hides a step in its "
                f"qualifier ({edge.technical_qualifier!r}); store the intermediate as a node")

    return problems


# ----------------------------------------------------------------------------- the graph

class ChemicalRouteGraph:
    def __init__(self, nodes: Iterable[ChemicalNode], edges: Iterable[RouteEdge],
                 raw_materials: Iterable[str] | None = None):
        self.nodes: dict[str, ChemicalNode] = {n.code: n for n in nodes if n.active}
        self.edges: list[RouteEdge] = [e for e in edges if e.active]
        self._into: dict[str, list[RouteEdge]] = {}
        for edge in self.edges:
            if edge.relation in PRODUCING:
                self._into.setdefault(edge.to_chemical, []).append(edge)
        # Which of these chemicals HTC actually forecasts. It is a commercial decision, not a
        # technical one - toluene is on the graph and nobody buys a toluene forecast from us - so it
        # is declared in the seed rather than inferred from the shape of the routes.
        self.raw_materials: tuple[str, ...] = tuple(
            code for code in (raw_materials or ()) if code in self.nodes
        )

    # -- basic reads
    def node(self, code: str) -> ChemicalNode | None:
        return self.nodes.get(code)

    def route_ids(self) -> list[str]:
        return sorted({edge.route_id for edge in self.edges})

    def edges_in_route(self, route_id: str) -> list[RouteEdge]:
        return [edge for edge in self.edges if edge.route_id == route_id]

    def inputs_to(self, code: str) -> list[RouteEdge]:
        """Direct production inputs to a chemical, across every route."""
        return list(self._into.get(code, ()))

    def unreachable(self) -> list[str]:
        """Governed chemicals that no edge touches. Each one is a series nobody can use."""
        touched = {e.from_chemical for e in self.edges} | {e.to_chemical for e in self.edges}
        return sorted(code for code in self.nodes if code not in touched)

    # -- the candidate generator
    def upstream_candidates(self, target: str, *, max_priced_steps: int = 3,
                            route_ids: Iterable[str] | None = None) -> list[Candidate]:
        """Chemicals upstream of `target`, ordered by distance in price-tracked nodes.

        Unpriced intermediates are traversed but do not count toward the distance, because a chemical
        nobody quotes cannot transmit an observable price. Chlorine reaches TDI through phosgene and
        is therefore one priced step away, not two reactions away.

        This returns what is worth TESTING as a predictor. It does not claim any of it predicts
        anything - that is for the correlation work to establish, and most of these will fail.
        """
        if target not in self.nodes:
            return []
        allowed = set(route_ids) if route_ids else None

        best: dict[str, Candidate] = {}
        # (chemical, priced steps so far, chemical steps so far, path, routes taken)
        queue: deque = deque([(target, 0, 0, (target,), ())])
        while queue:
            code, priced, steps, path, routes = queue.popleft()
            for edge in self._into.get(code, ()):
                if allowed is not None and edge.route_id not in allowed:
                    continue
                source = edge.from_chemical
                if source in path:
                    continue                      # a cycle; the path already covers it
                node = self.nodes.get(source)
                if node is None:
                    continue
                next_priced = priced + (1 if node.price_tracked else 0)
                if next_priced > max_priced_steps:
                    continue
                next_path = path + (source,)
                next_routes = routes + (edge.route_id,)
                if node.price_tracked:
                    current = best.get(source)
                    if current is None or next_priced < current.priced_steps:
                        best[source] = Candidate(
                            chemical=source, priced_steps=next_priced, chemical_steps=steps + 1,
                            route_ids=tuple(dict.fromkeys(next_routes)), path=next_path,
                            root_class=node.root_class,
                        )
                queue.append((source, next_priced, steps + 1, next_path, next_routes))

        return sorted(best.values(), key=lambda c: (c.priced_steps, c.chemical_steps, c.chemical))

    def priced_links(self, target: str, *, max_priced_steps: int = 3) -> list["PricedLink"]:
        """Consecutive pairs of quoted chemicals along the routes into `target`.

        The chain is sequential and the study was not. Testing crude oil, naphtha and toluene each
        directly against TDI reports one signal travelling down a chain as three findings, because
        crude moves naphtha which moves toluene. A link is the honest unit: the step from one quoted
        chemical to the next quoted chemical downstream of it, with whatever captive intermediates
        lie between recorded but not measured.
        """
        distance = {c.chemical: c.priced_steps
                    for c in self.upstream_candidates(target, max_priced_steps=max_priced_steps)}
        distance[target] = 0
        found: dict[tuple[str, str], dict] = {}
        for product, steps in distance.items():
            # Walk back from this quoted chemical through captive intermediates only, and stop at
            # the first quoted chemical on each path: that is the link. Naphtha reaches toluene
            # through reformate and through pygas; that is one link by two paths, not two links, so
            # the paths are merged and both routes are named.
            queue: deque = deque([(product, (), ())])
            visited: set[tuple[str, tuple[str, ...]]] = set()
            while queue:
                code, via, routes = queue.popleft()
                for edge in self._into.get(code, ()):
                    source = edge.from_chemical
                    node = self.nodes.get(source)
                    if node is None or source == product or source in via:
                        continue
                    next_routes = routes + (edge.route_id,)
                    if node.price_tracked:
                        if distance.get(source) == steps + 1:
                            held = found.setdefault((source, product),
                                                    {"via": via, "routes": set(), "paths": 0})
                            held["routes"].update(next_routes)
                            held["paths"] += 1
                            if len(via) < len(held["via"]):
                                held["via"] = via
                        continue                  # a quoted chemical ends the walk either way
                    key = (source, via)
                    if key in visited:
                        continue
                    visited.add(key)
                    queue.append((source, via + (source,), next_routes))

        links = [
            PricedLink(from_chemical=source, to_chemical=product, via=held["via"],
                       route_ids=tuple(sorted(held["routes"])),
                       priced_steps_to_target=distance[source], paths=held["paths"])
            for (source, product), held in found.items()
        ]
        links.sort(key=lambda link: (-link.priced_steps_to_target, link.from_chemical))
        return links

    def upstream_network(self, target: str, *, max_chemical_steps: int = 4,
                         route_ids: Iterable[str] | None = None) -> tuple[list[str], list[RouteEdge]]:
        """Every chemical and edge on the production paths into `target`, unpriced steps included.

        `upstream_candidates` answers "what could predict this price" and therefore skips chemicals
        nobody quotes. This answers a different question - "how is this made" - so phosgene and DNT
        belong in it. Distance is counted in reactions here, because that is what a reader of a route
        diagram is looking at.
        """
        if target not in self.nodes:
            return [], []
        allowed = set(route_ids) if route_ids else None
        seen_nodes: dict[str, None] = {target: None}
        seen_edges: dict[tuple[str, str, str, str], RouteEdge] = {}
        frontier = [(target, 0)]
        while frontier:
            code, depth = frontier.pop(0)
            if depth >= max_chemical_steps:
                continue
            for edge in self._into.get(code, ()):
                if allowed is not None and edge.route_id not in allowed:
                    continue
                if edge.from_chemical not in self.nodes:
                    continue
                seen_edges.setdefault(edge.key, edge)
                if edge.from_chemical not in seen_nodes:
                    seen_nodes[edge.from_chemical] = None
                    frontier.append((edge.from_chemical, depth + 1))
        return list(seen_nodes), list(seen_edges.values())

    def roots_of(self, target: str) -> dict[str, list[str]]:
        """Which origin classes a raw material's chains terminate in, and through which chemicals."""
        out: dict[str, list[str]] = {}
        for candidate in self.upstream_candidates(target, max_priced_steps=99):
            node = self.nodes.get(candidate.chemical)
            if node is not None and node.is_root and node.root_class:
                out.setdefault(node.root_class, []).append(candidate.chemical)
        return {k: sorted(v) for k, v in sorted(out.items())}


# ----------------------------------------------------------------------------- seed loading

DEFAULT_SEED = Path("data/config/chemical_routes_v1_1.json")


def load_raw_materials(path: Path | str = DEFAULT_SEED) -> tuple[str, ...]:
    """The chemicals the seed declares as raw materials HTC forecasts."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return tuple(str(code) for code in payload.get("raw_materials", ()))


def load_seed(path: Path | str = DEFAULT_SEED) -> tuple[list[ChemicalNode], list[RouteEdge]]:
    """Read the governed seed. The seed is a file, not hand-entry: 119 edges typed into a form is an
    error factory, and a file is reviewable in a diff."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    nodes = [
        ChemicalNode(
            code=str(row["code"]),
            display_name=str(row.get("display_name") or row["code"]),
            price_tracked=bool(row.get("price_tracked", True)),
            root_class=row.get("root_class") or None,
            is_root=bool(row.get("is_root", False)),
            aliases=tuple(row.get("aliases") or ()),
            note=str(row.get("note") or ""),
            full_name=str(row.get("full_name") or ""),
            cas=str(row.get("cas") or ""),
        )
        for row in payload.get("nodes", [])
    ]
    edges = [
        RouteEdge(
            route_id=str(row["route_id"]),
            from_chemical=str(row["from_chemical"]),
            relation=str(row["relation"]),
            to_chemical=str(row["to_chemical"]),
            technical_qualifier=str(row.get("technical_qualifier") or ""),
            note=str(row.get("note") or ""),
        )
        for row in payload.get("edges", [])
    ]
    return nodes, edges


def load_graph(path: Path | str = DEFAULT_SEED) -> ChemicalRouteGraph:
    nodes, edges = load_seed(path)
    return ChemicalRouteGraph(nodes, edges, load_raw_materials(path))
