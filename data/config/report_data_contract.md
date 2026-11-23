# PU Raw Material Price Monitor - Data Contract
Version 1.0, 10 October 2026. Held by the application.

This half is engineering. It says what the application guarantees and what it checks, and it
settles nothing about how the report reads. The editorial standard is a separate document and it
belongs to the editor.

## 1. What the application owns

The period, the markets, every price, every movement, every range, the comparison with the
preceding period, the plant roster, the pipeline, the dated plant events and the forecast.

These arrive as a fact pack. Every figure carries the exact words that place it in time, decided
where the evidence is and never where the sentence is written:

    market            TOLUENE, China
    open 1169  close 1310  low 1160  high 1317
    move_percent      12.1
    currency USD      unit MT
    period            Q3 2026, 2026-07-01 to 2026-09-30
    first_observation 2026-09-05
    last_observation  2026-09-30
    observations      26
    band              PARTIAL
    scope_label       between 5 September 2026 and 30 September 2026
    comparison_label  the preceding quarter holds no price for this market

## 2. How a scope label is decided

Let the period run from P0 to P1 and let L be its length in days.

    FULL      an observation on or before P0 + L/10, and one on or after P1 - L/10
              label: over <period name>
    PARTIAL   two or more observations, and the FULL test fails
              label: between <first observation> and <last observation>
    SINGLE    one observation
              label: on <observation date>, and the verb becomes "last traded at"
    NONE      no observation
              label: not priced in this edition

A PARTIAL figure is printed in full. Its movement, range, high and low are all shown. Only the
words that scope it change. Thin coverage changes the wording and never removes a correct figure.

The same applies to a population: where the roster records a plant as a range, the sum is a range.
To a comparison: where the preceding period rests on fewer than five readings, the label says so.
To geography: a price recorded for East China is labelled East China.

## 3. What the application checks

Three findings are about whether a figure can be traced. The application raises them and the
editor decides.

- **NUMBER.** A figure in the narrative that is not in the fact pack, character for character.
- **SCOPE.** A phrase placing a figure in time that is not one of the supplied scope labels.
- **SOURCE.** An assessor, news desk, trade publication, consultancy, index or data vendor named
  anywhere, in any form.

Everything else the application notices - house style, jargon, repetition, an unfinished
sentence - is reported as a note on the writing. It is never enforced, because the narrative is
editorial and the editor settles it.

A draft carrying a NUMBER, SCOPE or SOURCE finding is sent back once with the point raised, and
whatever comes back the second time goes to the editor as written.

## 4. What the engine returns

    {"headline": "", "opening": "", "observed": {"<market>": ""},
     "background": {"<market>": ""}, "route": "", "expectation": ""}

`observed` and `background` are keyed by the market name as the fact pack writes it.

## 5. Pictures, currency and the file

Charts are the reporting period and nothing before it. One market, one picture. The forecast is
the only line that runs past the last day of the period.

The chain diagram is the one the application already draws, with every chemical in it and each
one saying whether it has a market price.

One currency and one unit throughout, USD and MT unless the cover states otherwise. Evidence is
kept in the currency it was published in and each reading is converted at the rate for its own
observation date. Opening, closing, movement, range and percentage reconcile with each other and
with the chart.

Editable DOCX, Calibri, body 12 pt minimum. Copyright in the footer with the page number against
the right margin.

## 6. The reference base

An edition enters only after the editor has approved it. A retrieved edition is a record of what
HTC Global stated on a date and never a source of a figure: a number reaches a new edition
through the fact pack alone. Each file carries market, geography, period, currency, issue date
and version as retrievable attributes. An edition is immutable; a correction enters as a new
version and the one it replaces is marked.
