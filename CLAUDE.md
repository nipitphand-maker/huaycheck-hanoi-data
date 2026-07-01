# CLAUDE.md — project memory for huaycheck-hanoi-data

Auto-published JSON of **Hanoi lottery** results (Special / VIP / Normal) for the
HuayCheck app. A GitHub Action scrapes daily and commits `data/hanoi.json`; the
app reads that raw JSON. The fragile scrape lives **here**, never in the app.

## ⚠️ Read before editing `scrape.py`

`scrape.py` carries **two independent feature sets that must both survive every
edit**. They were once lost because a change was written against an old copy:

1. **Soft-block defenses** — multi-egress `fetch()` (direct → `allorigins` →
   `codetabs` raw-HTML mirrors), rotated User-Agents, browser-like headers,
   jittered backoff; `fetch()` returns `(html, ok)`; transient-block vs
   layout-change exit logic; the **staleness alarm** (`MAX_DATA_AGE_DAYS`).
2. **History accumulation** — `load_existing_history()` / `merge_history()` build
   `history.{cat}` (newest-first, deduped by date, capped at `HISTORY_LIMIT`).

**ALWAYS branch from the latest `main` before touching `scrape.py`.** Editing an
older snapshot silently reverts whichever feature is newer. Never use a `git`
copy or another session's stale checkout as the base.

## How failures are meant to behave (do NOT "simplify" this away)

- **No source reachable** (every egress soft-blocked) → keep last good data,
  **exit 0**. A single blocked run is a non-event; the other daily crons cover it.
- **Reachable *directly* but nothing parsed** → **exit 1** immediately = genuine
  layout change (`[error] sources reachable but no fresh draws parsed`). Only a
  **direct** hit trips this — a mirror can return a marker-containing but
  unparseable variant, so mirror-only misses are treated as unreachable (exit 0),
  not as breakage. `fetch()` returns `(html, ok, trusted)`; `trusted` is the
  direct-only signal. Don't collapse it back to a plain "any reachable" flag.
- **Published data older than `MAX_DATA_AGE_DAYS`** → **exit 1** (staleness alarm).
  So a **red workflow means data is genuinely stale**, not a one-off blip.
- The output file is written **only when there are real draws**, so `draws` and
  `history` are never clobbered with an empty result.

## Workflows

- `.github/workflows/scrape.yml` — 5 spread-out crons (20:00–00:00 Thai); each is
  a fresh runner IP, so all-blocked-at-once is unlikely. First success commits,
  the rest no-op. `timeout-minutes: 10`.
- `.github/workflows/autofix-scrape.yml` — when scrape goes red, runs Claude Code
  to classify the failure and, **only for a real layout change**, patch the parser
  against the live page, verify, and open a PR. Transient blocks are left alone.
  **Requires repo secret `ANTHROPIC_API_KEY`** (set 2026-06-30) and
  `id-token: write` permission (added in PR #5) — claude-code-action@v1 exchanges
  an OIDC token and fails before doing any work without it.

## Sources & timing

- Draws ~19:00–19:30 Thai (UTC+7); results settle ~20:00 Thai (13:00 UTC).
- `press.in.th/hanoi-lotto/` — primary; 4-digit + dated table.
- `ruayy.one/hanoi-{special,vip}-lottery-result/` — cross-check Special, sole
  source for VIP (VIP has no 4-digit → `verified:false`).

## Out of scope

- **ลาวพัฒนา / Laos Pattana** results in the app come from a **different repo /
  scraper** — not handled here.
- The app caches the raw JSON (raw.githubusercontent `max-age=300`); if the app
  shows stale data while `data/hanoi.json` is fresh, that's app-side caching —
  fix in the app (cache-bust the fetch), not here.

## Local run

```
pip install -r requirements.txt
python scrape.py        # writes data/hanoi.json
```
