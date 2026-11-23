"""The chain of questions for a gap, generated rather than scripted.

A case names what is missing. From that, and from what is already known about it, the five kinds of
question become five concrete ones, each pointed at a capability the application actually has.
Nothing here is written per event type: a new sort of unknown needs a row in `_ASKS`, not new code.

Stefan, 17 October 2026: "not mechanically, meaning now you use it for this plant price increase,
but it needs to work for any unknown item."

The order is by cost. Our own roster and our own prices answer first because they cost nothing and
cannot be refused; a producer's website is a fetch that may fail; a person is a week of waiting. A
chain that can be settled from what we already hold never touches the network.

W5 is always last and always the same question. Its answer is the one ending that is not a failure:
no disclosure obligation, publisher policy anonymises, below the resolution of our own series,
nobody assesses this market at all. Each of those is a collection requirement, which is actionable,
where "unattributed, rejected" was not.
"""
from __future__ import annotations

from app.investigation import model as m

# One row per gap. Each entry is (question, how it is asked, what could answer it).
#
# The wording matters: it is what a reader sees on the case, and it has to say what was actually
# asked rather than naming a procedure.
_ASKS: dict[str, tuple[tuple[str, str, str], ...]] = {
    m.ACTOR: (
        (m.W1, "Does the observation name a producer we already know, in either language?",
         m.OUR_EVENTS),
        (m.W1, "Which producer or plant is this, and what do we already know about where it is?",
         m.ROSTER),
        (m.W2, "Which plants could it be, from our own roster for this chemical and geography?",
         m.ROSTER),
        (m.W2, "Which of those candidates is listed and therefore obliged to disclose it?",
         m.FILINGS),
        (m.W2, "Has any candidate said so on its own newsroom?", m.PRODUCER_PRESS),
        (m.W3, "Did our own price series move in a way consistent with this happening?",
         m.OUR_PRICES),
        (m.W3, "Has any candidate been named in the news we already curate?", m.NEWSLETTER),
        (m.W4, "Does the size of the move fit one candidate's capacity rather than another's?",
         m.ROSTER),
        (m.W5, "Why would nobody name it: no disclosure duty, publisher policy, or too small to "
               "matter?", m.NOTHING),
    ),
    m.AMOUNT: (
        (m.W1, "What was announced, and in what unit, if any?", m.OUR_EVENTS),
        (m.W2, "Has the producer stated the number itself anywhere?", m.PRODUCER_PRESS),
        (m.W2, "Did our own weekly briefing carry a figure for it?", m.BRIEFING),
        (m.W3, "How far did our own series for that market actually move?", m.OUR_PRICES),
        (m.W4, "What percentage of the base price would any stated figure be?", m.OUR_PRICES),
        (m.W5, "Why would the number not be public: a confidential customer letter, settled only "
               "in transactions?", m.NOTHING),
    ),
    m.DATE: (
        (m.W1, "What is undated - the event, or when it takes effect?", m.OUR_EVENTS),
        (m.W2, "Does the producer's own announcement give an effective date?", m.PRODUCER_PRESS),
        (m.W3, "When did our own series for that market turn?", m.OUR_PRICES),
        (m.W4, "Does the move line up with the contract round for this market?", m.OUR_PRICES),
        (m.W5, "Why would no date be stated: effective 'as contracts allow'?", m.NOTHING),
    ),
    m.PLACE: (
        (m.W1, "What geography is named, and how precisely?", m.ROSTER),
        (m.W2, "Which sites exist in that geography for this chemical?", m.ROSTER),
        (m.W3, "Which of our markets for this chemical moved?", m.OUR_PRICES),
        (m.W4, "Does the regional capacity share fit the size of what was reported?", m.ROSTER),
        (m.W5, "Why would the site not be named: a region is how the publisher reports it?",
         m.NOTHING),
    ),
    m.CAUSE: (
        (m.W1, "Is this move cost-led or demand-led? Nobody ever states it, so it is inferred.",
         m.ROUTES),
        (m.W3, "Did the FEEDSTOCK move first, or did the product move while the feedstock sat "
               "still?", m.OUR_PRICES),
        (m.W4, "What does the mass balance say the input cost did over the same period?", m.ROUTES),
        (m.W2, "Have the producers said anything about it in their own results commentary?",
         m.PRODUCER_PRESS),
        (m.W3, "Did our own briefing or the newsletter give a reason?", m.BRIEFING),
        (m.W5, "Why would the cause not be stated: motive is never announced, only inferred?",
         m.NOTHING),
    ),
    m.SCOPE: (
        (m.W1, "Which chemicals and which geographies does this actually touch?", m.ROUTES),
        (m.W2, "What did the publisher's own scope line say, verbatim?", m.BRIEFING),
        (m.W4, "How far up or down the route does it reach, from the chain itself?", m.ROUTES),
        (m.W3, "Which of our own series show it?", m.OUR_PRICES),
        (m.W5, "Why would the scope be vague: written for a general reader?", m.NOTHING),
    ),
    m.MARKET: (
        (m.W1, "Is there any publisher who prices this chemical in this geography?", m.OUR_PRICES),
        (m.W2, "Does any producer publish its own price for it?", m.PRODUCER_PRESS),
        (m.W3, "Do we collect its feedstocks, so the route at least exists for us?", m.ROUTES),
        (m.W4, "Is it assessed inside another grade rather than on its own?", m.ROUTES),
        (m.W5, "Why would no assessment exist: a market priced only in bilateral contracts has no "
               "public price?", m.NOTHING),
    ),
}


def steps_for(gap: str) -> tuple[m.Step, ...]:
    """The chain for this gap, cheapest capability first, W5 last whatever it costs.

    W5 is not sorted with the rest. It is the question that explains an absence, and asking it
    before the cheap enquiries have run would let a chain excuse itself before it has looked.
    """
    asks = _ASKS.get(gap)
    if not asks:
        raise KeyError(f"no chain is defined for the gap {gap!r}; see model.GAPS")
    body = [a for a in asks if a[0] != m.W5]
    last = [a for a in asks if a[0] == m.W5]
    body.sort(key=lambda a: (m.COST[a[2]], a[0]))
    return tuple(m.Step(question=q, asked=asked, capability=capability)
                 for q, asked, capability in body + last)


def unanswerable(step: m.Step) -> bool:
    """A step with no capability behind it is a collection requirement, not an enquiry."""
    return step.capability == m.NOTHING
