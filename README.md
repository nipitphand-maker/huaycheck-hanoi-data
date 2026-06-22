# huaycheck-hanoi-data

Auto-published JSON of **Hanoi lottery** results (Special / VIP / Normal) for the
HuayCheck app. Mirrors the khiemdoan XSMB pattern: a GitHub Action scrapes the
results daily and commits `data/hanoi.json`; the app reads that raw JSON.

The fragile scrape lives **here**, never in the app — if a source changes layout,
fix the bot and re-run; installed apps keep reading clean JSON, no store release.

## Consume

```
https://raw.githubusercontent.com/<owner>/huaycheck-hanoi-data/main/data/hanoi.json
```

```jsonc
{
  "generatedAt": "2026-06-22T06:59:39Z",
  "draws": {
    "vn_hanoi_special": { "date": "2026-06-21", "num4": "8427", "top3": "427", "top2": "27", "bottom2": "80", "sources": ["press","ruayy"], "verified": true },
    "vn_hanoi":         { "date": "2026-06-21", "num4": "8083", "top3": "083", "top2": "83", "bottom2": "33", "sources": ["press"], "verified": true },
    "vn_hanoi_vip":     { "date": "2026-06-21", "num4": null,   "top3": "602", "top2": "02", "bottom2": "97", "sources": ["ruayy"], "verified": false }
  }
}
```

## Trust rules for the app

- **Only use a draw if `date` is recent** (the bot already guards, guard again client-side).
- `verified: true` = cross-checked across ≥2 independent sources (currently only
  `vn_hanoi_special` and `vn_hanoi` reach this).
- `vn_hanoi_vip` is **single-source (ruayy)** and has **no 4-digit** — treat as lower
  confidence; consider showing manual-entry fallback.

## Sources (verified 2026-06-22)

- `press.in.th/hanoi-lotto/` — primary; clean dated table with 4-digit. Its
  `vn_hanoi` value was cross-validated == official XSMB last-4.
- `ruayy.one/hanoi-{special,vip}-lottery-result/` — cross-check for Special, sole
  source for VIP. Same LottoVIP/Ruay affiliate ring as press (agreement = parse
  correctness, not independent ground truth).

## Run locally

```
pip install -r requirements.txt
python scrape.py    # writes data/hanoi.json
```

Cost: $0 (GitHub Actions free tier). Maintenance: fix `scrape.py` regex if a source
changes its page.
