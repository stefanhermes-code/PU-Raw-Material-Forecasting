"""What an investigation is: a gap, five kinds of question, and the capabilities that answer them.

Stefan, 17 October 2026, on an unattributed observation: "you do not throw away the observation. You
dig deeper... You need to use the 5 times why method before you give up. Not one enquiry." And on
the shape of it: "not mechanically, meaning now you use it for this plant price increase, but it
needs to work for any unknown item."

So the five are KINDS of question, chosen by what sort of thing is missing, not a script per event
type. Each answer re-asks what is still missing, and a chain ends on a REASON or stays open.

And the map that answers them is Stefan's, not an invented table: "chemical, feedstocks, geography,
capacity, plants. Isnt that the map you need?" It is. Every gap is a hole in one of those five and
the other four are what close it.

    gap            W2 who would know          W3 our own trace         W4 what it implies
    ----------------------------------------------------------------------------------------
    ACTOR          plants, owners, listings   a step in our prices     capacity vs the move
    AMOUNT         the producer's own letter  the size of the move     percent of the base
    DATE           the contract cycle         when our series turned   the next round
    CAUSE          producers' own commentary  did the FEEDSTOCK move   stoichiometry
    PLACE          plants by geography        which market moved       freight and routes
    SCOPE          the publisher's own words  which series carry it    the chain's reach
    MARKET         who assesses it at all     the route is collected   contract-only trade

W5 is the same question every time - why might the answer not exist at all - and its answer is the
one ending that is not a failure.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------------- what is missing
ACTOR = "ACTOR"        # who did it: an anonymised producer, "a certain Northwest TDI plant"
AMOUNT = "AMOUNT"      # how much: an increase announced with no number
DATE = "DATE"          # when: effective from unstated
PLACE = "PLACE"        # where: a region named, not a site
CAUSE = "CAUSE"        # why: cost-led or demand-led, which nobody ever states
SCOPE = "SCOPE"        # what it covers: which chemicals, which geographies
MARKET = "MARKET"      # whether a market exists at all: nobody assesses this one
GAPS = (ACTOR, AMOUNT, DATE, PLACE, CAUSE, SCOPE, MARKET)

# ------------------------------------------------------------------------------- the five questions
W1 = "W1"   # what exactly is unknown - name the gap before anything else
W2 = "W2"   # who would know, and who is obliged to say
W3 = "W3"   # what trace would it leave in what we already hold
W4 = "W4"   # what does it imply, and does the implication narrow it
W5 = "W5"   # why might the answer not exist at all
QUESTIONS = (W1, W2, W3, W4, W5)

QUESTION_WORDS = {
    W1: "What exactly is unknown?",
    W2: "Who would know, and who is obliged to say?",
    W3: "What trace would it leave in what we already hold?",
    W4: "What does it imply, and does the implication narrow it?",
    W5: "Why might the answer not exist at all?",
}

# ------------------------------------------------------------------------------- what can answer it
#
# Only capabilities the application actually has. A question with no capability behind it is not an
# enquiry, it is a collection requirement, and saying so is the point of W5.
ROSTER = "ROSTER"                  # our capacity files: plants, provinces, owners, kt
ROUTES = "ROUTES"                  # the chemical route graph: feedstocks and mass coefficients
OUR_PRICES = "OUR_PRICES"          # 34,000 readings we collected ourselves
OUR_EVENTS = "OUR_EVENTS"          # what the engine has already heard
FILINGS = "FILINGS"                # a listed producer's own disclosure, via cninfo
PRODUCER_PRESS = "PRODUCER_PRESS"  # the producer's own newsroom
NEWSLETTER = "NEWSLETTER"          # Global Newspilot, 133 companies watched
BRIEFING = "BRIEFING"              # HTC's own weekly briefing and special reports
PERSON = "PERSON"                  # a named person said so. Clint's freight differential.
NOTHING = "NOTHING"                # no capability can answer this - a collection requirement
CAPABILITIES = (ROSTER, ROUTES, OUR_PRICES, OUR_EVENTS, FILINGS, PRODUCER_PRESS, NEWSLETTER,
                BRIEFING, PERSON, NOTHING)

# Cheapest and most certain first. Our own data costs nothing and cannot be refused; a producer's
# website is a fetch that may fail; a person is a week of waiting. A chain that can be settled from
# the roster and our own prices never goes near the network.
COST = {ROSTER: 0, ROUTES: 0, OUR_PRICES: 1, OUR_EVENTS: 1, BRIEFING: 2, NEWSLETTER: 3,
        FILINGS: 4, PRODUCER_PRESS: 5, PERSON: 9, NOTHING: 99}

# ------------------------------------------------------------------------------------ case outcomes
OPEN = "OPEN"
NARROWED = "NARROWED"        # not named, but down to a stated few, and publishable as that
RESOLVED = "RESOLVED"        # named, with the trail of what said so
EXPLAINED = "EXPLAINED"      # why the answer does not exist. A finding, not a failure.
STATES = (OPEN, NARROWED, RESOLVED, EXPLAINED)

PENDING = "PENDING"
ANSWERED = "ANSWERED"
NARROWED_BY = "NARROWED"
EXHAUSTED = "EXHAUSTED"          # we looked where it should be and it is not there
UNANSWERABLE = "UNANSWERABLE"    # nothing we have could answer it; W5's honest ending
OUTCOMES = (PENDING, ANSWERED, NARROWED_BY, EXHAUSTED, UNANSWERABLE)


@dataclass(frozen=True)
class Step:
    """One question in a chain: which of the five, what it asks, and what could answer it."""

    question: str
    asked: str
    capability: str


@dataclass
class Case:
    """An observation we cannot fully explain, and the work done on it so far."""

    gap: str
    observed: str
    origin_kind: str = "EVENT"
    observed_at: str = ""
    source_id: str = ""
    evidence_id: str = ""
    origin_ref: str = ""
    chemical: str = ""
    geography: str = ""
    state: str = OPEN
    answer: str = ""
    id: str = ""
    data: dict = field(default_factory=dict)
