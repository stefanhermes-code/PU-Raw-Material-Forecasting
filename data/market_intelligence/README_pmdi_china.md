# China polymeric MDI register

Rebuilt 17 September 2026 from the isocyanate market letter of 1 July 2026, which carries a
plant-by-plant China nameplate table whose rows sum exactly to its printed China sub-total of
5,290 kt. It is the only plant-level MDI source HTC holds; the EUROPUR report has narrative on MDI
but no plant table.

The letter is a licensed subscription publication and may not be reproduced, so every row is
attributed on the page as **Source not disclosed**. The document is named in `internal_note`, which
never reaches a customer screen.

## What is in it

| plant | province | owner | kt |
|---|---|---|---|
| Wanhua, Ningbo | Zhejiang | Wanhua | 1,800 (two lines, 1,000 + 800) |
| Wanhua, Yantai | Shandong | Wanhua | 1,100 |
| Wanhua, Fujian (Fuqing) | Fujian | Wanhua | 800 |
| Covestro, Shanghai | Shanghai | Covestro | 600 |
| BASF, Chongqing | Chongqing | BASF | 400 |
| Huntsman, Shanghai (Caojing) | Shanghai | Huntsman | 350, crude MDI |
| BASF, Shanghai | Shanghai | BASF | 240 |

China total 5,290 kt. Wanhua group 3,700 kt, 70%.

## What the first version got wrong

Built 16 September 2026, it read 3,910 kt. Every difference is recorded per row in `internal_note`:

- **Wanhua Yantai was left out entirely**, on my statement that no source broke it out by site. This
  one does, at 1,100 kt. The page understated China by more than a quarter on a premise that was
  false when I wrote it.
- **BASF Shanghai was left out** as most likely BASF's share of the Caojing joint venture. The
  letter lists BASF Shanghai and Huntsman Shanghai as two plants.
- Ningbo 1,500 -> 1,800, Chongqing 530 -> 400, Caojing 480 -> 350: older readings replaced.

## The Fuqing expansion

Wanhua has approval to take the site from 800 kt to about 1.5 Mt. **No source states when.** The
EUROPUR report says in terms that it is unsure when the additional 700 kt will enter full
production, and the letter of 1 July 2026 still describes the plant as 800,000 t/yr when it went
down for maintenance on 23 June 2026.

The developments register carried a row saying the revamp completed in June 2026. Nobody reported
that. It was written from the pipeline's own due date and has been removed. The pipeline entry now
carries no due date and says the timing was never stated.

## How to correct it

Edit the CSVs. The page reads them on every load. A row must name a `source`, or the loader drops
it; the document behind it goes in `internal_note`.

