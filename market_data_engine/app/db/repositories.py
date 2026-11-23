from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.models import MarketIdentityModel, PriceObservationModel

# The Incoterm says which end of the journey the price is quoted at, so it says which field carries
# the meaning: CFR, CIF, DEL, FD, DAP and DDP name a destination, FOB, EXW and FCA name an origin.
# Stefan, 26 September 2026, and the store already follows it wherever a connector wrote anything -
# CFR 24 of 24, DELIVERED 40 of 40, FD and FOB all of them.
#
# Two connectors did not. `jlc_pu_channel` left both fields empty for its first three days and began
# filling them on the fourth, and `echemi_tdi` writes whatever the page calls the producer, which on
# a page with no producer named is the literal word UNKNOWN. Between them they created eight
# identities that were duplicates of four real ones, split over a field that meant nothing, and put
# "DAP UNKNOWN" into a chart legend.
#
# Normalising here rather than in each connector means a connector written next month cannot repeat
# it.
# One copy of the rule, in the module both sides of the application can see. The Forecast card
# needs the same answer to name a geography correctly, and a second copy here would be free to
# drift from it.
from app.domain.series_key import COMPARABILITY_FIELDS, comparability_group
from raw_material_forecasting.market_basis import (
    NAMES_A_DESTINATION as _NAMES_A_DESTINATION,
    NAMES_AN_ORIGIN as _NAMES_AN_ORIGIN)
_MEANS_NOTHING = {"", "UNKNOWN", "NONE", "N/A", "NA", "NOT SPECIFIED", "NOT_SPECIFIED", "UNSPECIFIED"}


def _stated(value):
    text = str(value or "").strip()
    return None if text.upper() in _MEANS_NOTHING else text


def normalise_route(commercial_basis, market_geography, origin, destination):
    """Origin and destination as the Incoterm says they should be.

    A placeholder is cleared, and the field the Incoterm gives meaning to is filled from the market
    geography when the connector left it empty. The other field is left exactly as the connector
    wrote it: a real origin on a CFR price is worth keeping, it simply is not what the price is
    quoted at.
    """
    origin, destination = _stated(origin), _stated(destination)
    basis = str(commercial_basis or "").strip().upper()
    geography = _stated(market_geography)
    if basis in _NAMES_A_DESTINATION and destination is None:
        destination = geography
    elif basis in _NAMES_AN_ORIGIN and origin is None:
        origin = geography
    return origin, destination


# Nothing calls this class. The live store path is `app/persistence/identity.py`, whose
# `MATCH_FIELDS` is the same eight fields plus origin and destination; `normalise_route` above is
# what the rest of the engine imports from this module. Left in place rather than deleted while A
# is open, but its `ensure_identity` is not the rule the store follows - do not read it as one.
class PriceRepository:
    def __init__(self, session: Session):
        self.session = session

    def ensure_identity(self, *, product_family, market_geography, commercial_basis, currency, unit, observation_type, product_grade_or_chemistry=None, packaging=None, origin=None, destination=None):
        origin, destination = normalise_route(commercial_basis, market_geography, origin, destination)
        # What the store matches on IS the series key, so it is built from the one field list
        # rather than typed out again. An incoming reading joins an existing series when all eight
        # agree and mints a new one otherwise - and `comparability_group` is the same list without
        # the price kind, so the two can no longer drift apart.
        fields = dict(product_family=product_family, product_grade_or_chemistry=product_grade_or_chemistry,
                      market_geography=market_geography, commercial_basis=commercial_basis,
                      packaging=packaging, currency=currency, unit=unit,
                      observation_type=observation_type)
        comp = comparability_group({k: fields[k] for k in COMPARABILITY_FIELDS})
        stmt = select(MarketIdentityModel).where(
            *[getattr(MarketIdentityModel, name) == fields[name] for name in fields])
        obj = self.session.scalar(stmt)
        if obj:
            return obj
        obj = MarketIdentityModel(product_family=product_family, product_grade_or_chemistry=product_grade_or_chemistry, origin=origin, destination=destination, market_geography=market_geography, commercial_basis=commercial_basis, packaging=packaging, currency=currency, unit=unit, observation_type=observation_type, comparability_group=comp)
        self.session.add(obj)
        self.session.flush()
        return obj

    def add_price(self, *, identity_id, source_id, evidence_id, price, available_from, observation_date=None, publication_date=None, weight=1.0):
        row = PriceObservationModel(identity_id=identity_id, source_id=source_id, evidence_id=evidence_id, price=price, available_from=available_from, observation_date=observation_date, publication_date=publication_date, weight=weight)
        self.session.add(row)
        self.session.flush()
        return row

    def list_as_of(self, identity_id: str, cutoff):
        stmt = select(PriceObservationModel).where(PriceObservationModel.identity_id == identity_id, PriceObservationModel.available_from <= cutoff).order_by(PriceObservationModel.available_from)
        return list(self.session.scalars(stmt))
