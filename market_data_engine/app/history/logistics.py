"""Backing the logistics out of a delivered price, to get an ex-works equivalent.

A purchase order into Bangkok is not a China ex-works price, and the difference is a stack of real
costs: haulage to the port, terminal handling, ocean freight, insurance, and for a DDP order the
whole import side as well. Deducting them gives an estimate of what the same tonne was worth at the
gate in China.

ESTIMATE IS THE OPERATIVE WORD, and it is why every component is named separately here rather than
folded into one spread. A single "USD 100/t of logistics" cannot be argued with. A line that says
ocean freight Shanghai to Laem Chabang is USD 900 a tank can be - somebody who books tanks will know
within a week whether that is right, and can change one number instead of re-deriving everything.

None of these figures came from a quotation. They are market-typical 2024 costs for a 22-tonne ISO
tank of a hazardous liquid on an intra-Asia lane, and they should be replaced by real ones the moment
real ones exist. `costs_per_tonne` returns the stack alongside the answer so a screen can show the
working rather than a conclusion.

THE DUTY LINE IS THE BIG UNCERTAINTY. TDI enters Thailand from China at zero under the ASEAN-China
FTA when a Form E certificate of origin travels with the shipment, and at the MFN rate without one.
The default here is zero with Form E, because that is what a regular contracted flow between a
Chinese producer and a Thai converter would use, and because assuming otherwise would silently add
five per cent to every derived price. It is a flag, not a fact.

WHAT THIS DOES NOT DO
---------------------
It does not produce a market price. A delivered contract price minus an estimated freight stack is an
estimate of a contract price at the factory gate, and a contract price is not a spot assessment -
it is negotiated, it lags, and it can sit either side of the domestic market. Anything computed here
is evidence to weigh, and it is kept out of the collected price series for that reason.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# One ISO tank of TDI, nominal. The orders run 22, 24 and 44 tonnes, so a tank is not a fixed
# quantity and a flat divisor would charge a fuller tank the same freight per tonne as an emptier
# one. `tanks_for` counts the tanks and the fixed costs are then spread over what actually shipped.
TANK_TONNES = 22.0


def tanks_for(tonnes: float) -> int:
    """How many tanks a shipment took. 22 and 24 tonnes are one; 44 is two."""
    return max(1, round(tonnes / TANK_TONNES)) if tonnes else 1


@dataclass(frozen=True)
class CostStack:
    """Every line between the factory gate in China and the buyer's tank farm, in USD per tank."""

    # --- origin side: in every delivered price, whatever the Incoterm -------------------------
    inland_to_port: float = 350.0        # plant to Shanghai, tank haulage
    export_handling: float = 250.0       # export declaration, terminal handling, documentation
    ocean_freight: float = 900.0         # Shanghai to Laem Chabang, ISO tank, hazardous
    insurance_rate: float = 0.001        # marine cover, share of cargo value

    # --- destination side: DDP only, where the seller carries the import ----------------------
    import_duty_rate: float = 0.0        # zero under ACFTA with Form E; the MFN rate without one
    import_clearance: float = 250.0      # Thai customs entry, port and terminal charges
    inland_from_port: float = 300.0      # Laem Chabang to Rat Burana
    tank_handling: float = 200.0         # detention, cleaning, repositioning

    label: str = "market-typical 2024, intra-Asia ISO tank"

    def per_tonne(self, incoterm: str, cargo_value_usd_per_tonne: float,
                  tonnes: float = TANK_TONNES) -> tuple[float, dict]:
        """(total USD per tonne to deduct, the lines that make it up).

        The Incoterm decides how far down the stack to go. CIF stops at the discharge port; DDP runs
        to the buyer's gate with duty paid. EXW deducts nothing - it is already the answer.
        """
        basis = str(incoterm or "").strip().upper()
        lines: dict[str, float] = {}
        if basis in {"EXW", "FCA"}:
            return 0.0, lines

        # Per tank, spread over the tonnes that actually shipped rather than over a nominal tank.
        shipped = tonnes or TANK_TONNES
        per_tonne = tanks_for(shipped) / shipped
        lines["Inland to Shanghai port"] = self.inland_to_port * per_tonne
        lines["Export handling and documentation"] = self.export_handling * per_tonne
        lines["Ocean freight to Laem Chabang"] = self.ocean_freight * per_tonne
        lines["Marine insurance"] = cargo_value_usd_per_tonne * self.insurance_rate

        if basis in {"DDP", "DDU", "DAP", "DPU"}:
            lines["Import duty"] = cargo_value_usd_per_tonne * self.import_duty_rate
            lines["Thai clearance and port charges"] = self.import_clearance * per_tonne
            lines["Inland to Rat Burana"] = self.inland_from_port * per_tonne
            lines["Tank handling and return"] = self.tank_handling * per_tonne
        elif basis not in {"CIF", "CFR", "C&F", "CPT", "CIP", "DELIVERED"}:
            # An Incoterm nobody taught this module. Say so rather than guess how far it reaches.
            return 0.0, {"unrecognised Incoterm": 0.0}

        return sum(lines.values()), lines


DEFAULT_STACK = CostStack()


def ex_works_equivalent(delivered_price: float, incoterm: str, *, usd_rate: float,
                        tonnes: float = TANK_TONNES,
                        stack: CostStack = DEFAULT_STACK) -> dict:
    """Take a delivered price in its own currency back to an ex-works estimate in that currency.

    `usd_rate` is units of the price's currency per USD on the date of the order, so the freight
    stack is converted at the same moment the price was struck rather than at today's rate.
    """
    value_usd = delivered_price / usd_rate if usd_rate else 0.0
    deduction_usd, lines = stack.per_tonne(incoterm, value_usd, tonnes)
    deduction = deduction_usd * usd_rate
    return {
        "delivered": round(delivered_price, 2),
        "incoterm": incoterm,
        "deduction": round(deduction, 2),
        "deduction_usd_per_tonne": round(deduction_usd, 2),
        "ex_works_estimate": round(delivered_price - deduction, 2),
        "share_of_price": round(deduction / delivered_price, 4) if delivered_price else None,
        "lines_usd_per_tonne": {name: round(value, 2) for name, value in lines.items()},
        "basis": stack.label,
        "estimated": True,
    }
