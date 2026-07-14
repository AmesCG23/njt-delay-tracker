# Social card assets

Inputs for `src/og_card.py`, which redraws `docs/og-card.png` each day
with the live cumulative delay cost.

- `og-card-template.png` — the 1200×630 masthead with an empty middle
  band. The number and context line are drawn into the band at runtime.
  Regenerate this file if the brand/wordmark changes.
- `EBGaramond-Bold-subset.ttf` — EB Garamond instanced at weight 700 and
  subset to `$0123456789,.` (used for the dollar figure).
- `EBGaramond-MediumItalic-subset.ttf` — EB Garamond Italic instanced at
  weight 500, ASCII subset (used for the context line).
- `OFL.txt` — the SIL Open Font License covering the EB Garamond subsets.

Test a render locally without any credentials:

```
python src/og_card.py --total 8412067 --out /tmp/test-card.png
```
