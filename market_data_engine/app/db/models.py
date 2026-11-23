from __future__ import annotations
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, JSON, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _id(): return str(uuid4())
def _now(): return datetime.now(timezone.utc)

class Base(DeclarativeBase):
    pass

class SourceRegistryModel(Base):
    __tablename__ = "source_registry"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    source_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    publisher: Mapped[str] = mapped_column(String)
    source_name: Mapped[str] = mapped_column(String)
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    rights_class: Mapped[str] = mapped_column(String)
    rights_status: Mapped[str] = mapped_column(String)
    collection_method: Mapped[str] = mapped_column(String, default="HTML")
    refresh_frequency: Mapped[str] = mapped_column(String, default="DAILY")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    reliability_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_successful_collection: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

class IngestionRunModel(Base):
    __tablename__ = "ingestion_run"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    source_id: Mapped[str] = mapped_column(String, index=True)
    state: Mapped[str] = mapped_column(String)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

class RawEvidenceModel(Base):
    __tablename__ = "raw_evidence"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    source_id: Mapped[str] = mapped_column(String, index=True)
    ingestion_run_id: Mapped[str | None] = mapped_column(String, ForeignKey("ingestion_run.id"), nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(String, index=True)
    content_location: Mapped[str] = mapped_column(Text)
    publication_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    collection_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    rights_status: Mapped[str] = mapped_column(String)

class MarketIdentityModel(Base):
    __tablename__ = "market_identity"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    product_family: Mapped[str] = mapped_column(String, index=True)
    product_grade_or_chemistry: Mapped[str | None] = mapped_column(String, nullable=True)
    origin: Mapped[str | None] = mapped_column(String, nullable=True)
    destination: Mapped[str | None] = mapped_column(String, nullable=True)
    market_geography: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    commercial_basis: Mapped[str | None] = mapped_column(String, nullable=True)
    packaging: Mapped[str | None] = mapped_column(String, nullable=True)
    currency: Mapped[str] = mapped_column(String)
    unit: Mapped[str] = mapped_column(String)
    observation_type: Mapped[str] = mapped_column(String)
    comparability_group: Mapped[str] = mapped_column(String, index=True)

class PriceObservationModel(Base):
    __tablename__ = "price_observation"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    identity_id: Mapped[str] = mapped_column(String, ForeignKey("market_identity.id"), index=True)
    source_id: Mapped[str] = mapped_column(String, index=True)
    evidence_id: Mapped[str] = mapped_column(String, index=True)
    price: Mapped[float] = mapped_column(Float)
    observation_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    publication_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    available_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    # The currency this reading is priced in. On the READING since migration 0059, because a TDI
    # China price quoted in yuan and one quoted in dollars are the same series (masterplan A,
    # item 5) - so currency identifies nothing and describes this number. The column of the same
    # name on `market_identity` is the currency of record for the readings a series holds.
    currency: Mapped[str] = mapped_column(String, default="", server_default="")
    # Which supplier's product this reading is of, where the record named one. On the READING since
    # migration 0060 (masterplan A, item 3): a product name is not a chemical name, so it never
    # identifies a market - but six of HTC's purchase products sit in one market, and without this
    # collapsing the grade field would have turned them into six anonymous readings.
    # `raw_material_forecasting.chemical_vocabulary.PRODUCTS` is the register it names.
    supplier_product: Mapped[str] = mapped_column(String, default="", server_default="")
    # What was actually paid or printed, before anything was done to it. `price` is the stored series
    # on the basis the store keeps it in - converted where a series has had its FX effect taken out -
    # and these two keep the original so a conversion can be rechecked or redone. Null means the two
    # are the same and nothing was applied.
    native_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    native_currency: Mapped[str | None] = mapped_column(String, nullable=True)
    # The published range, on exactly the basis `price` is on: same currency, same unit, same tax
    # treatment. A publisher that prints one number leaves both null - null is "no range given",
    # never "the range was zero". Anything that converts `price` converts all three or none.
    price_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_high: Mapped[float | None] = mapped_column(Float, nullable=True)

class PriceInCurrencyModel(Base):
    """One reading, expressed in one currency.

    Not another market - same product, same terms, same timeline, a different unit of account
    (Stefan, 27 September 2026). It hangs off the reading rather than becoming an identity of its
    own, because the chart groups readings by terms, packaging and region and pays no attention to
    currency; a converted copy filed as its own identity would be averaged with the reading it came
    from.

    Written the moment a datapoint is created, so nothing is converted while a page is drawing. The
    rate and the day it was quoted are kept with it, so any value can be rechecked against the ECB.
    A reading is stored in its own currency too, at a rate of 1, so a reader never has to know which
    currency a market happens to be assessed in.

    No row means no rate was available for that day - the SE Asia weeks before the earliest fixing
    we hold. Nothing is drawn for those, which is the honest answer.
    """
    __tablename__ = "price_in_currency"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    observation_id: Mapped[str] = mapped_column(
        String, ForeignKey("price_observation.id"), index=True)
    currency: Mapped[str] = mapped_column(String, index=True)
    price: Mapped[float] = mapped_column(Float)
    price_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    fx_rate: Mapped[float] = mapped_column(Float)
    fx_rate_date: Mapped[Date] = mapped_column(Date)


class FeatureObservationModel(Base):
    __tablename__ = "feature_observation"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    source_id: Mapped[str] = mapped_column(String, index=True)
    evidence_id: Mapped[str] = mapped_column(String)
    feature_type: Mapped[str] = mapped_column(String, index=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    available_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

class EventRecordModel(Base):
    __tablename__ = "event_record"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    source_id: Mapped[str] = mapped_column(String, index=True)
    evidence_id: Mapped[str] = mapped_column(String)
    event_type: Mapped[str] = mapped_column(String, index=True)
    material: Mapped[str | None] = mapped_column(String, nullable=True)
    state: Mapped[str] = mapped_column(String, default="ANNOUNCED")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    available_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

class MarketReferencePointModel(Base):
    __tablename__ = "market_reference_point"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    identity_id: Mapped[str] = mapped_column(String, ForeignKey("market_identity.id"), index=True)
    reference_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reference_value: Mapped[float] = mapped_column(Float)
    version: Mapped[int] = mapped_column(Integer)
    observation_ids: Mapped[list] = mapped_column(JSON, default=list)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

class DerivedValueModel(Base):
    """A number this engine computed, kept where it can never be mistaken for a price.

    `price_observation` is what a publisher printed. `market_reference_point` is the governed value
    consolidated from those prints. A cost floor is neither: it is computed from OTHER chemicals'
    prices through the route graph and its mass coefficients, and if it shared a table with either
    of the other two it would eventually be read, charted or sold as a market price.

    It records what it was made from for the same reason every register in this store does: the
    route it came down, the coefficients used, and the ids of the input readings. A derived number
    whose inputs cannot be listed is an assertion, and this store does not keep assertions.

    Stored rather than computed on demand, so the question "what did the floor say in March, and
    what did the market do next" can be asked at all. That comparison is the only way a floor earns
    the right to inform a forecast.
    """

    __tablename__ = "derived_value"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    identity_id: Mapped[str] = mapped_column(String, ForeignKey("market_identity.id"), index=True)
    value_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    value: Mapped[float] = mapped_column(Float)
    # What kind of derived number this is - a cost floor today, something else later. Never a price.
    kind: Mapped[str] = mapped_column(String, index=True, default="COST_FLOOR")
    # The route it came down, e.g. PES-ALIPH, so the derivation can be followed on the graph.
    route_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # {chemical: {"coefficient": x, "price": y, "observation_id": z}} - the whole working.
    inputs: Mapped[dict] = mapped_column(JSON, default=dict)
    currency: Mapped[str] = mapped_column(String, default="CNY")
    unit: Mapped[str] = mapped_column(String, default="t")
    version: Mapped[int] = mapped_column(Integer, default=1)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class QualityIssueModel(Base):
    __tablename__ = "quality_issue"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    source_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    issue_type: Mapped[str] = mapped_column(String, index=True)
    severity: Mapped[str] = mapped_column(String, default="WARNING")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class QuarantinedReadingModel(Base):
    """A reading that did not become a market, kept with the publisher's own words.

    Masterplan D, v1.2.11. One row per (source, field, value) rather than per reading: a document
    import that misreads a column misreads it on every row, and a row per reading would bury the one
    fact a reader needs - this publisher writes this value and we do not know it - under a thousand
    copies of it. `readings` counts how many it has stopped; `example` keeps one whole reading as
    first read, which is what a reader needs to judge the value without going back to the document.

    `settled_at` makes it a queue rather than a bin: naming the value in
    `identity_vocabulary.RAW_MATERIALS`, or fixing the connector, settles the row and the reading
    arrives on the next collection. Seen again afterwards, it opens again.
    """
    __tablename__ = "quarantined_reading"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    source_id: Mapped[str] = mapped_column(String, default="", server_default="", index=True)
    field: Mapped[str] = mapped_column(String)
    value: Mapped[str] = mapped_column(String, default="", server_default="")
    reason: Mapped[str] = mapped_column(Text, default="", server_default="")
    example: Mapped[dict] = mapped_column(JSON, default=dict)
    document: Mapped[str] = mapped_column(Text, default="", server_default="")
    page: Mapped[str] = mapped_column(String, default="", server_default="")
    readings: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Kept as the day, as a string, the way every other date a publisher gave us is kept on a
    # reading: what matters is which day it was seen, not the minute.
    first_seen: Mapped[str] = mapped_column(String, default="", server_default="")
    last_seen: Mapped[str] = mapped_column(String, default="", server_default="")
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="", server_default="")


class FxRateModel(Base):
    """Daily ECB reference rate: 1 unit of `base` = `rate` units of `currency` on `rate_date`."""
    __tablename__ = "fx_rate"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    rate_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    base: Mapped[str] = mapped_column(String, default="EUR")
    currency: Mapped[str] = mapped_column(String, index=True)
    rate: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String, default="ECB")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ReferenceSeriesModel(Base):
    """A non-price series held for analysis: an index, a proxy, a benchmark from another market.

    Deliberately NOT a `market_identity`. A PPI index has no currency and no tonne, so it cannot be
    converted, cannot be averaged with a price, and must never reach a market reference or a customer
    view. Keeping it in its own table makes that structural rather than a rule someone has to
    remember - the price path cannot read this table by accident.

    What it is for: correlation. The log change of a true price index equals the log change of the
    price it tracks, so an index measures co-movement perfectly well even though it can never quote a
    number of dollars.
    """
    __tablename__ = "reference_series"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    series_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    chemical: Mapped[str] = mapped_column(String, index=True)      # chemical route graph node code
    label: Mapped[str] = mapped_column(String)
    geography: Mapped[str] = mapped_column(String, index=True)
    unit: Mapped[str] = mapped_column(String)                      # "Index 1982=100", "USD/metric tonne"
    is_index: Mapped[bool] = mapped_column(Boolean, default=True)
    # STRONG | USABLE_PROXY | WEAK_PROXY - carried from the pack and shown wherever the series is.
    quality: Mapped[str] = mapped_column(String, default="USABLE_PROXY", index=True)
    source_type: Mapped[str] = mapped_column(String, default="")
    publisher: Mapped[str] = mapped_column(String, default="")
    source_url: Mapped[str] = mapped_column(String, default="")
    rights_class: Mapped[str] = mapped_column(String, default="PUBLIC_ATTRIBUTION")
    note: Mapped[str] = mapped_column(String, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ReferenceObservationModel(Base):
    """One dated value on a reference series. `value` is in the series' own unit, never converted."""
    __tablename__ = "reference_observation"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    series_id: Mapped[str] = mapped_column(String, ForeignKey("reference_series.series_id"), index=True)
    observation_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    value: Mapped[float] = mapped_column(Float)
    evidence_id: Mapped[str] = mapped_column(String, unique=True, index=True)

class ForecastVintageModel(Base):
    """What a model said, on the day it said it. Append-only.

    A forecast is a claim with a date on it. The moment it is overwritten by a newer one, the claim
    stops being checkable - and a forecast nobody can check afterwards is not a forecast, it is a
    number on a screen. Everything the application has shown so far has been computed on the spot and
    thrown away, so there is no record of what any horizon actually promised, and "the band holds
    eight times in ten" could be asserted for weeks because nothing on disk could contradict it.

    One row per identity, model, horizon and as-of date, written once and never updated. The outcome
    is appended later, when the period it forecast has closed, into the columns reserved for it. That
    separation is the point: the forecast row cannot be quietly adjusted to fit what happened,
    because writing the outcome does not rewrite the claim.

    `state` is the release state the forecast was produced under - a forecast made while the model
    was a research candidate is evidence about the model and was never on a customer's screen, and
    the two must never be scored together.
    """
    __tablename__ = "forecast_vintage"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)

    # Identity of the claim. Unique together, which is what makes the table append-only in practice:
    # a second write for the same claim is a duplicate, not an update.
    vintage_key: Mapped[str] = mapped_column(String, unique=True, index=True)
    identity_key: Mapped[str] = mapped_column(String, index=True)
    model_key: Mapped[str] = mapped_column(String, index=True)
    model_version: Mapped[str] = mapped_column(String, default="")
    horizon_weeks: Mapped[int] = mapped_column(Integer, index=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    # What was said.
    chemical: Mapped[str] = mapped_column(String, default="", index=True)
    currency: Mapped[str] = mapped_column(String, default="")
    value: Mapped[float] = mapped_column(Float)
    quantiles: Mapped[dict] = mapped_column(JSON, default=dict)
    period: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    steps: Mapped[int] = mapped_column(Integer, default=1)
    resolution: Mapped[str] = mapped_column(String, default="M")
    exact: Mapped[bool] = mapped_column(Boolean, default=False)

    # How much the claim rested on, kept with the claim rather than recomputed. A calibration scale
    # measured on sixteen origins is a different thing from one measured on a hundred, and a year
    # from now the only way to know which this was is to have written it down.
    calibration: Mapped[dict] = mapped_column(JSON, default=dict)
    observations: Mapped[int] = mapped_column(Integer, default=0)
    anchor_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    # RELEASED | CANDIDATE | CONTROL - what this forecast was for when it was made.
    state: Mapped[str] = mapped_column(String, default="CANDIDATE", index=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    # Appended when the period closes. Null means not yet answerable, which is a different state from
    # answered and wrong, and the difference matters when counting how often a band held.
    outcome_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[float | None] = mapped_column(Float, nullable=True)
    inside_band: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Method provenance (Adaptive Forecast Method Selection CR). Null until Workpackage 3 writes
    # them: which governed method made this forecast, who chose it, and the review it rested on.
    selected_method_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("forecast_method.id"), nullable=True, index=True)
    selection_source: Mapped[str | None] = mapped_column(String, nullable=True)
    review_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("model_fit_review.id"), nullable=True, index=True)


class GovernedForecastRunModel(Base):
    """One run of the method in force for one series and horizon (WP3, decision D5). Append-only.

    SUCCEEDED points at the immutable vintage it wrote. FAILED carries the reason and the automatic
    recommendation the page shows beside the failure (Review Focus 3); the failed method's name is
    never put on another method's number.
    """
    __tablename__ = "governed_forecast_run"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    run_key: Mapped[str] = mapped_column(String, unique=True, index=True)
    identity_id: Mapped[str] = mapped_column(String, ForeignKey("market_identity.id"), index=True)
    identity_key: Mapped[str] = mapped_column(String, index=True)
    horizon_weeks: Mapped[int] = mapped_column(Integer, index=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    method_id: Mapped[str] = mapped_column(String, ForeignKey("forecast_method.id"), index=True)
    method_key: Mapped[str] = mapped_column(String)
    method_version: Mapped[str] = mapped_column(String, default="")
    selection_source: Mapped[str] = mapped_column(String)
    review_id: Mapped[str | None] = mapped_column(String, ForeignKey("model_fit_review.id"), nullable=True)
    status: Mapped[str] = mapped_column(String, index=True)          # SUCCEEDED | FAILED
    reason: Mapped[str] = mapped_column(Text, default="")
    vintage_id: Mapped[str | None] = mapped_column(String, ForeignKey("forecast_vintage.id"), nullable=True)
    recommended_method_key: Mapped[str | None] = mapped_column(String, nullable=True)
    inputs: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ForecastGovernanceSettingModel(Base):
    """A named governance setting (WP4, decision D7): value, who set it, why, and every earlier value."""
    __tablename__ = "forecast_governance_setting"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    actor: Mapped[str] = mapped_column(String, default="SYSTEM")
    reason: Mapped[str] = mapped_column(Text, default="")
    history: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ----------------------------------------------------------------------------------------------
# The investigation. An observation we cannot fully explain is a CASE, not a reject.
#
# Stefan, 17 October 2026: "If it is unattributed you do not throw away the observation. You dig
# deeper... You need to use the 5 times why method before you give up." And on the shape of it:
# "not mechanically... it needs to work for any unknown."
#
# What made this necessary: the signal lane decided an anonymous producer report was unpublishable
# and wrote it to a sidecar nobody reads, and ten price series read 4 to 57 times their own level
# for weeks with nothing asking why. Both are the same fault - a judgement where a question
# belonged.

class InvestigationCaseModel(Base):
    __tablename__ = "investigation_case"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)

    # What kind of thing is missing. The whole chain is generated from this, so it is named
    # precisely rather than lumped under "unattributed", which was the old pipeline's one bucket
    # for every sort of gap.
    gap: Mapped[str] = mapped_column(String, index=True)

    # The observation itself, verbatim and in its own language. Never a generated summary: a
    # rendered English sentence is what made "a producer price update was issued" out of a headline
    # that said "Southeast Asia regional price bulletin", and reasoning over the summary is how
    # three days were spent on the wrong question.
    observed: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Where it came from, so the primary source can always be re-read.
    source_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    # The hash of the document, the same thing `price_observation.evidence_id` and
    # `event_record.evidence_id` hold, and like them it carries no foreign key. It is not
    # `raw_evidence.id`: one document read by five importers is five rows sharing one hash, so the
    # target could not be unique even if we wanted the constraint. 0051 declared
    # ForeignKey("raw_evidence.id") here and SQLite never enforced it; on Postgres it made the
    # Open questions page answer 500 to every press, because no event's hash is any row's id
    # (0 of 505, measured). Dropped in 0069.
    evidence_id: Mapped[str | None] = mapped_column(String, nullable=True)
    origin_kind: Mapped[str] = mapped_column(String)          # EVENT | PRICE | DOCUMENT | PERSON
    origin_ref: Mapped[str | None] = mapped_column(String, nullable=True)

    # What we already know. Either may be absent - that is often the gap itself.
    chemical: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    geography: Mapped[str | None] = mapped_column(String, index=True, nullable=True)

    # OPEN | NARROWED | RESOLVED | EXPLAINED. There is deliberately no REJECTED and no DISCARDED.
    # EXPLAINED is a real ending: it says WHY the answer does not exist - no disclosure obligation,
    # publisher policy anonymises, below the resolution of our own series - which is a finding and
    # a collection requirement, not a failure.
    state: Mapped[str] = mapped_column(String, default="OPEN", index=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)

    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class InvestigationEnquiryModel(Base):
    __tablename__ = "investigation_enquiry"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    case_id: Mapped[str] = mapped_column(String, ForeignKey("investigation_case.id"), index=True)

    # Which of the five, and in what order it was asked. The five are KINDS of question, chosen by
    # the gap, not a script per event type.
    question: Mapped[str] = mapped_column(String)
    step: Mapped[int] = mapped_column(Integer, default=1)

    # The question in words, and which of our capabilities can answer it.
    asked: Mapped[str] = mapped_column(Text)
    capability: Mapped[str] = mapped_column(String)

    # PENDING | ANSWERED | NARROWED | EXHAUSTED | UNANSWERABLE. EXHAUSTED means we looked and it is
    # not there; UNANSWERABLE means no source we have could ever answer it, which is the collection
    # requirement that comes out of a chain terminating honestly.
    outcome: Mapped[str] = mapped_column(String, default="PENDING", index=True)
    found: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    ran_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class InvestigationCandidateModel(Base):
    __tablename__ = "investigation_candidate"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    case_id: Mapped[str] = mapped_column(String, ForeignKey("investigation_case.id"), index=True)

    # A suspect, from our own map. "How many northwest TDI plants are there?" - the roster answers
    # it, and the case opens with three named plants instead of a blank (Stefan, 17 October 2026).
    name: Mapped[str] = mapped_column(String)
    owner: Mapped[str | None] = mapped_column(String, nullable=True)
    chemical: Mapped[str | None] = mapped_column(String, nullable=True)
    geography: Mapped[str | None] = mapped_column(String, nullable=True)
    province: Mapped[str | None] = mapped_column(String, nullable=True)
    capacity_kt: Mapped[float | None] = mapped_column(Float, nullable=True)

    # CANDIDATE | RULED_OUT | CONFIRMED, and why - a suspect ruled out says what ruled it out.
    state: Mapped[str] = mapped_column(String, default="CANDIDATE", index=True)
    why: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_source: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ----------------------------------------------------------------------------------------------
# Forecast-method governance (Adaptive Forecast Method Selection CR, Workpackage 1, October 2026).
#
# `forecast_vintage` keeps what a method said. These tables keep what the application knows about
# the METHODS: which exist, what each may do, how each did in a Model Fit Review, which one is in
# force for each series and horizon, and who overrode or promoted what. Additive; nothing here
# changes a live forecast until Workpackage 3.

class ForecastMethodModel(Base):
    """One approved configuration of a forecasting method."""
    __tablename__ = "forecast_method"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    method_key: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String, default="")
    kind: Mapped[str] = mapped_column(String, default="UNIVERSAL", index=True)   # UNIVERSAL | CONDITIONAL | CONTROL
    status: Mapped[str] = mapped_column(String, default="CHALLENGER", index=True) # ForecastMethodStatus
    version: Mapped[str] = mapped_column(String, default="1")
    is_flat_control: Mapped[bool] = mapped_column(Boolean, default=False)
    commercial_use: Mapped[str] = mapped_column(String, default="UNKNOWN")       # licence, apart from status
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")
    # Every status change, oldest first: {"at", "from", "to", "actor", "reason"}.
    status_history: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ModelFitReviewModel(Base):
    """One run of the tournament over every eligible series and horizon at one cutoff."""
    __tablename__ = "model_fit_review"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String, default="RUNNING", index=True)     # ReviewStatus
    actor: Mapped[str] = mapped_column(String, default="SYSTEM")
    notes: Mapped[str] = mapped_column(Text, default="")


class ModelFitResultModel(Base):
    """How one method did on one series and horizon inside one review. Permanent."""
    __tablename__ = "model_fit_result"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    review_id: Mapped[str] = mapped_column(String, ForeignKey("model_fit_review.id"), index=True)
    identity_id: Mapped[str] = mapped_column(String, ForeignKey("market_identity.id"), index=True)
    horizon_weeks: Mapped[int] = mapped_column(Integer, index=True)
    method_id: Mapped[str] = mapped_column(String, ForeignKey("forecast_method.id"), index=True)
    method_version: Mapped[str] = mapped_column(String, default="")
    execution_status: Mapped[str] = mapped_column(String, default="SUCCEEDED")     # SUCCEEDED | FAILED | INELIGIBLE
    matched_mae: Mapped[float | None] = mapped_column(Float, nullable=True)
    directional_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    matched_dates: Mapped[int] = mapped_column(Integer, default=0)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class ForecastMethodSelectionModel(Base):
    """The method in force for one series and one horizon, and where that choice came from."""
    __tablename__ = "forecast_method_selection"
    # At most one ACTIVE selection per series and horizon, enforced by the store (WP4 gate, v0.5.65):
    # two reviews committing at once cannot both leave a row active.
    __table_args__ = (Index("ux_forecast_method_selection_active", "identity_id", "horizon_weeks", unique=True,
                            sqlite_where=text("active = 1"), postgresql_where=text("active")),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    identity_id: Mapped[str] = mapped_column(String, ForeignKey("market_identity.id"), index=True)
    horizon_weeks: Mapped[int] = mapped_column(Integer, index=True)
    method_id: Mapped[str] = mapped_column(String, ForeignKey("forecast_method.id"), index=True)
    method_version: Mapped[str] = mapped_column(String, default="")
    source: Mapped[str] = mapped_column(String, index=True)                        # SelectionSource
    review_id: Mapped[str | None] = mapped_column(String, ForeignKey("model_fit_review.id"), nullable=True, index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actor: Mapped[str] = mapped_column(String, default="SYSTEM")
    reason: Mapped[str] = mapped_column(Text, default="")


class ForecastMethodOverrideModel(Base):
    """An administrator's choice for one series and horizon. Never replaced automatically."""
    __tablename__ = "forecast_method_override"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    identity_id: Mapped[str] = mapped_column(String, ForeignKey("market_identity.id"), index=True)
    horizon_weeks: Mapped[int] = mapped_column(Integer, index=True)
    method_id: Mapped[str] = mapped_column(String, ForeignKey("forecast_method.id"), index=True)
    actor: Mapped[str] = mapped_column(String)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    removed_by: Mapped[str] = mapped_column(String, default="")
    removal_reason: Mapped[str] = mapped_column(Text, default="")


class ChallengerApprovalModel(Base):
    """An administrator promoting a Challenger to Production Eligible. Explicit, audited."""
    __tablename__ = "challenger_approval"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    method_id: Mapped[str] = mapped_column(String, ForeignKey("forecast_method.id"), index=True)
    actor: Mapped[str] = mapped_column(String)
    reason: Mapped[str] = mapped_column(Text)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
