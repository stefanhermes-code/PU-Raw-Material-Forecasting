# PU Raw Material Price Monitor - Production Standard
Version 1.1, 10 October 2026. Issued by HTC Global.
Supersedes version 1.0 of 10 October 2026.

Change in this version: sections 1 and 5 are rewritten. The fact pack now
supplies the words that scope a number as well as the number itself, so a
statement can no longer claim a wider scope than its evidence. Section 16 is
new and governs the reference base.

## 0. What this governs

This standard governs every edition of the PU Raw Material Price Monitor,
whoever or whatever writes it. It binds the PU Observatory engine and the
PI3 report generator equally. Where the two produce different documents from
the same period, the difference is a defect in one of them.

The reader is a purchasing manager, a commercial manager or a plant manager
who buys or converts polyurethane raw materials. The document is written to
be sent to that reader without further editing.

## 1. The fact pack supplies the number and its scope

Before any prose is written, the producer is given a fact pack. Every figure
the edition is allowed to use arrives as a record, and every record carries
the scope of the figure and the exact words that state that scope.

A price movement record carries:

    market            TOLUENE, China
    basis             EXW
    currency          USD
    unit              MT
    open              1169
    close             1310
    low               1160
    high              1317
    move_absolute     141
    move_percent      12.1
    period            Q3 2026, 2026-07-01 to 2026-09-30
    first_observation 2026-09-05
    last_observation  2026-09-30
    observations      26
    coverage          0.28
    scope_label       between 5 September and 30 September 2026
    comparison        none, the preceding period holds no price for this market

Rules:

1. Every number in the finished document must match a value in the fact pack
   exactly, including its rounding.
2. Every phrase in the finished document that places a number in time, in a
   population, in a geography or on a price basis must be a scope_label taken
   from the fact pack, used word for word.
3. The producer never composes a scope phrase. The producer never writes
   "over Q3", "during the quarter", "across the market", "in China" or any
   other scoping words of its own invention, in prose, in a table heading, in
   an indicator caption or in a chart annotation.
4. A draft containing a number absent from the fact pack, or a scope phrase
   that matches no scope_label in the pack, is refused whole and written
   again. It is not corrected in place.
5. The producer writes sentences. The producer produces no arithmetic. A
   figure the document needs and the pack lacks is requested from the
   generator and added to the pack, or the sentence is dropped.

The reason for rule 3 is that the fault this standard exists to prevent is a
label, not a number. The toluene figure of 12.1 per cent was correct in both
reference editions. The word "quarter" attached to it was false. The layer
that holds the evidence is the only layer that may describe its extent.

## 2. Hard gates

A draft that fails any gate below is refused and written again.

**Number gate.** Section 1, rules 1 and 4.

**Scope gate.** Section 1, rules 2 and 3. Every scoping phrase in the rendered
text, including table headings, chart titles, chart annotations, indicator
captions and the headline, is matched against the scope labels in the pack.
An unmatched phrase is a refusal.

**Source gate.** No price assessor, news desk, trade publication, consultancy,
index or data vendor is named anywhere in the document, in any form, including
in a chart, a caption, a footnote or an abbreviation. The prices are HTC
Global's own market research under the PI3 Industrial Intelligence programme
and the document says so once, in one plain sentence, with no account of what
the research is or is not based on.

**Method gate.** The document carries nothing about how it was made.
Section 8 lists the banned vocabulary.

**Truncation gate.** No sentence in the rendered document may end without
terminal punctuation. Text is never cut to fit a box. Where text overruns,
the writing is shortened or the box is enlarged.

**Count gate.** Any count stated on a summary page is supplied by the
generator from the same set the body prints. A front page saying two
developments over a body describing six is a refusal.

## 3. What each edition must establish

For every selected material and market:

- Opening and closing price for the period, each with its observation date.
- Movement in money per tonne and in per cent, with its scope label.
- Highest and lowest price recorded inside the window, with its scope label.
- The preceding period's movement, with the number of observations behind it.
- Price history covering at least 12 months and preferably 24.
- The priced upstream material and the same figures for it.
- The developments that explain the move.
- Installed capacity, ownership concentration, announced additions,
  maintenance, curtailments and closures, from the register.
- What the move costs the buyer, in money.
- The forward view, quantified where the application produces one.

## 4. Structure

Page 1, cover. HTC Global and PI3 marks, title, market, period, reporting
currency and unit, issue date, and a headline of two or three sentences that
states the most important thing that happened. Four indicator figures may sit
on this page where the space allows.

Page 2, market view. A developed opening assessment. Four indicators: closing
price, period movement, period range, upstream movement. The forward view.
The buyer position. A quarterly comparison bar chart where the data supports
one.

Market detail, one section per material. Price chart. What the quarter did.
What was behind it. The market itself: who makes it, how much, how
concentrated, what is coming, what stopped. Purchasing consequence.

Cost chain and forward watch. Upstream movement and its relationship to the
monitored material. A production chain diagram where it adds information.
The specific developments to watch in the next period.

The document grows when the evidence justifies it. Content is never forced
into a page count and never compressed to reach one.

## 5. How scope labels are decided

The generator decides every scope label by the rules below. The producer
applies the result. Neither invents a label.

### 5.1 Time

Let the period run from P0 to P1 and let L be its length in days.

    FULL      an observation on or before P0 + L/10, and one on or
              after P1 - L/10
              label: over <period name>
              example: over Q3 2026

    PARTIAL   two or more observations, and the FULL test fails
              label: between <first observation> and <last observation>
              example: between 5 September and 30 September 2026

    SINGLE    exactly one observation
              label: on <observation date>
              the verb becomes "last traded at"

    NONE      no observation
              label: not priced in this edition
              no movement, range or comparison is stated

A PARTIAL figure is shown in full. Its movement, range, high and low are all
printed. Only its label changes. Thin coverage changes the wording. Thin
coverage never removes a market and never removes a correct figure.

A PARTIAL figure may not appear in a column headed with the period name, in
a chart annotation naming the period, or beside a FULL figure under a shared
period heading. Where an edition mixes FULL and PARTIAL figures in one table,
each row carries its own window.

### 5.2 The preceding period

The comparison carries the observation count behind it. Where that count is
below five, the label says so:

    label: the preceding quarter, on three readings

Where the preceding period is NONE, the comparison is stated as absent and
the figure stands alone.

### 5.3 Population

A figure summed across a set carries the size of the set and the completeness
of the set.

Where any member is recorded as a range, the sum is stated as a range:

    label: 1,910 to 2,110 kt across 11 plants

The top of a range is never printed as a point figure. Where every member is
a point figure, the sum is a point figure and the label carries the count.

A share of a population carries the same scope:

    label: 1,680 kt of that, held by three owners

### 5.4 Geography and basis

A price carries the geography the identity records, in the words the identity
records. A price recorded for East China is labelled East China. A price
recorded for China is labelled China. The producer never widens one to the
other and never narrows one to the other.

Where an edition carries more than one price basis, every price carries its
basis. FD is DAP; FD is written as Free Delivered (DAP) on first use.

Figures on different bases are never summed, averaged, compared as a single
series or drawn on one line.

### 5.5 Currency inside a scope

Evidence is kept in the currency it was published in. Each reading is
converted at the rate for its own observation date. A single period-end rate
is never applied across a series. A percentage computed in one currency is
never printed beside prices in another.

## 6. Charts

**Line and continuity.** A price history is a chronological line through the
recorded observations. Isolated markers standing in for a line are a defect.

**Cadence.** A break in the line is drawn only where the interval between two
observations is long relative to that series' own cadence. The cadence is
measured on the series' regular part. Where a series changes cadence inside
the window, for example monthly for twenty months and then daily for five
weeks, each stretch is judged against its own cadence. A monthly series is
never broken for being monthly. Assuming a daily rhythm and then declaring
the rest of the series missing is the fault this rule removes.

**Breaks.** Where a break is genuine, the line stops and the gap is left
visible. A straight line is never drawn across a hole.

**Axes.** The vertical axis is fitted to the data that is drawn. Every tick
drawn falls inside the plotted range. No tick, label or legend element is
drawn outside the plot area or over another element.

**The period must be visible.** The reporting period occupies at least a
quarter of the plot width, or the chart is accompanied by a second plot of
the period alone. A quarterly report whose quarter is eight per cent of the
chart has failed its own purpose.

**A series drawn only inside the shaded period** carries its own scope label
in the legend, so a reader cannot take a five-week line for a two-year one.

**Shading, colour and labels.** The period is shaded. Each material keeps one
colour across the whole edition. The axis label carries currency and unit.
The legend names only materials and their windows.

**Bar charts** carry period comparisons. Charts are rendered at print
resolution. Decorative graphics are not used.

**Every chart is checked against the fact pack** before it is placed,
including its first point, its last point, its high and its low.

## 7. Diagrams and other graphics

A diagram claims only what is true of it. Where a production chain highlights
stages, the highlight means one thing, that meaning is stated in the caption,
and every highlighted stage satisfies it. A caption reading "a filled step is
a market priced in this report" over four filled steps of which two carry a
price is a refusal.

A diagram fits its space. A chain that wraps and runs a connector back across
the full width into a half-empty row is redrawn to fit one row, or the step
boxes are made smaller.

## 8. Writing

Stefan Hermes' voice. Developed paragraphs. Lead with what happened, then why,
then what it costs. Short sentences carry the weight.

**Banned vocabulary.** These phrases and their variants do not appear:
the first reading, this edition as a description of the evidence, the record,
the register, recorded range, covered in this edition, movement measured
across it, evidence status, graded, confidence, methodology, method, model,
our data, our collection, assessment, validated, confirmed by a second source,
data gap, coverage, backfill, as reported by, according to.

**No furniture explaining itself.** A label reading PERIOD does not also say
"movement measured across it". A table of terms that defines market price,
contract price and producer posting is a method disclosure and is removed.

**No contrastive negation.** The forms "X rather than Y", "not X but Y",
"instead of" and "X is not Y" are not used. Say what is the case.

**No repetition.** No statement appears twice in a section in different words.

**Rising prices are not attributed to demand** unless the fact pack carries
demand evidence. Where offers rose and purchasing stayed thin, the document
says both and leaves the cause where the evidence puts it.

**Supplier offers and completed transactions are distinguished** in the facts,
without a paragraph teaching the reader to distinguish them.

**Confidence.** The document states what is known, plainly, and does not hedge
a figure the fact pack supports. A scope label is part of what is known and is
never dropped to sound more confident.

**ASCII.** Output is plain ASCII throughout, except for a currency symbol
where one is required.

## 9. The buyer sentence

Every material section carries the consequence in money, because buyers think
in cash, not in per cent.

Form: the move per tonne, then what it does to a stated volume.
"TDI in China closed the quarter USD 275 / MT above where it opened. On
500 tonnes a month that is USD 137,500 more a month at the closing price."

The volume used is the reader's own where it is known, and a round stated
figure where it is not. The percentage follows the money.

## 10. Forward view

Where the application produces a forecast for the material and market, the
edition carries it, in this form:

"Looking a month ahead, the method puts pMDI in China at USD 2,700 / MT,
within a range of USD 2,480 / MT to USD 2,950 / MT. That range is the width
that covered 80 per cent of this forecast."

Where the application produces no forecast for that market, the forward
section carries the developments to watch and says nothing numerical.

A forecast is never written, rounded differently, widened or narrowed by the
producer. It arrives in the fact pack or it is absent.

## 11. Currency and units

Reporting currency is USD and the unit is the metric tonne, written MT,
unless the edition states otherwise on its cover. One currency and one unit
throughout, in every chart, table, indicator and sentence.

Opening value, closing value, absolute movement, percentage movement and
range must reconcile with each other and with the chart, in the currency
shown.

## 12. Names and registers

Chinese plants are named with the city or province before the company:
Fujian Wanhua, Jingzhou Hualu Hengsheng, Cangzhou Dahua.

Every development the register holds inside the period is accounted for.
An item is left out only on a stated ground of relevance, never because the
section was full. Where exports to a region are named as the thing to watch,
an outage in that region inside the period is carried.

Geography never narrows the register. A Japanese closure and an Argentine
shutdown belong in a China edition when they move the balance, under a
heading that fits them.

## 13. Formatting

Editable DOCX. Calibri throughout. Body 12 pt minimum, headings 14 to 18 pt,
title 22 to 26 pt. Navy, blue and teal. Aligned tables with real cell padding.
Consistent margins, page numbers, header and footer.

Font size is never reduced to make content fit.

No page falls below forty per cent of its text area in use. A page carrying
two paragraphs is reflowed into the page before it, or the section is
expanded to earn its page. Headings do not stand alone at the foot of a page.

## 14. Quality control, performed and recorded

The producer performs every check below and records the result of each with
the evidence that produced it. A check that was not run is recorded as not
run. A check is never recorded as passed on the strength of an expectation.
Inventing a result, a measurement or a verification is prohibited absolutely.

The reader of this document does not perform these checks.

1. Every number in the document traced to the fact pack. Record the count
   checked and the count matched.
2. Every scoping phrase in the rendered text matched to a scope label in the
   pack. Record each phrase and the label it matched.
3. For every market, the coverage band recomputed from the observation dates
   and compared against the label that was used.
4. Opening, closing, movement, range and percentage reconciled, per market,
   in the reporting currency.
5. Every chart compared against the series it draws, including its first
   point, its last point, its high and its low.
6. Charts inspected for broken lines, stray ticks, overlapping elements and
   period share of width.
7. Diagram captions checked against the diagram.
8. Document rendered to PDF and every page looked at.
9. Page coverage measured; pages below the floor reflowed.
10. Every sentence in the rendered document checked for terminal punctuation.
11. Banned vocabulary searched for across the whole rendered text, including
    tables, captions, headers and footers.
12. Source names searched for across the whole rendered text.
13. Contrastive forms and repeated statements searched for.
14. Currency symbol and unit checked in every chart, table and sentence.
15. Plant names checked for city-before-company order.
16. Register coverage checked: every in-period item either present or
    recorded as deliberately omitted with its ground.

Any failed check is corrected and every check the correction could affect is
run again.

## 15. Output

An editable DOCX, publication ready, sent with the recorded quality control
results and the fact pack it was built from. The evidence register stays
internal. The quantitative workbook is supplied on request.

## 16. The reference base

Issued editions are kept in a reference base so later editions can refer to
what was said before.

1. An edition enters the base only after every check in section 14 has
   passed. An edition carrying a failed check is never uploaded.
2. A retrieved edition is a record of what HTC Global stated on a date. It is
   never a source of a number. Section 1 stands: a number reaches a new
   edition through the fact pack alone. A figure found in a retrieved edition
   and absent from the pack may not be written.
3. Each file carries market, geography, period, currency, issue date and
   version as retrievable attributes, so a search can be filtered.
4. The fact pack is uploaded beside its edition, so a continuity claim can be
   checked without reading prose for numbers.
5. An edition is immutable once issued. A correction enters as a new file
   with the same period and a higher version, and the superseded file is
   marked so retrieval can exclude it.
6. The base holds our own editions only. It is kept apart from any store the
   engine treats as independent market reference material.
