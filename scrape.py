#!/usr/bin/env python3
"""
HuayCheck Hanoi data bot — scrapes Hanoi Special / VIP / Normal results and
publishes a clean JSON the HuayCheck app reads (mirrors the khiemdoan XSMB pattern).

Sources (verified 2026-06-22):
  - press.in.th/hanoi-lotto/  : clean dated table, gives 4-digit + 3top/2top/2bot.
                                ฮานอยปกติ value cross-validated == official XSMB last-4.
  - ruayy.one/hanoi-*-result/ : cross-check for Special; sole source for VIP.

Design rules (do NOT break):
  - Output JSON only. The fragile scrape lives HERE, never in the app.
  - Freshness guard: never emit a stale row as today's. Carry the draw date so
    the app can guard too.
  - Cross-check Special across both sources; mark verified=false if they disagree
    or only one source is available (VIP is single-source).
"""
import json
import re
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CTX = ssl.create_default_context()

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/148.0 Safari/537.36"
TIMEOUT = 30
# Accept a draw only if its date is within this many days of the run — kills the
# "site shows yesterday, we stamp it today" failure mode.
MAX_STALE_DAYS = 3

PRESS_URL = "https://press.in.th/hanoi-lotto/"
RUAY = {
    "vn_hanoi_special": "https://ruayy.one/hanoi-special-lottery-result/",
    "vn_hanoi_vip": "https://ruayy.one/hanoi-vip-lottery-result/",
}
# press.in.th category header -> app category id
PRESS_CATS = {
    "ฮานอยพิเศษ": "vn_hanoi_special",
    "ฮานอยปกติ": "vn_hanoi",
}

TH_MONTHS = {
    "มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4, "พฤษภาคม": 5,
    "มิถุนายน": 6, "กรกฎาคม": 7, "สิงหาคม": 8, "กันยายน": 9, "ตุลาคม": 10,
    "พฤศจิกายน": 11, "ธันวาคม": 12,
}


def _fetch_once(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "th,en;q=0.8",
            "Accept-Encoding": "identity",
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX) as r:
        return r.read().decode("utf-8", "ignore")


def fetch(url, must_contain="งวดวันที่", attempts=3):
    """Fetch with retry. Datacenter IPs intermittently get a soft-block page
    (HTTP 200, no real data) instead of the result table — retry with backoff
    so a single bad response doesn't fail the whole run."""
    last = ""
    for i in range(attempts):
        try:
            html = _fetch_once(url)
        except Exception as e:
            print(f"[diag] {url} attempt {i + 1}: {e}", file=sys.stderr)
            time.sleep(3 * (i + 1))
            continue
        low = html.lower()
        challenge = any(x in low for x in ("just a moment", "cf-challenge", "cf_chl", "enable javascript and cookies", "attention required"))
        ok = (must_contain in html) and not challenge
        print(f"[diag] {url} attempt {i + 1} -> {len(html)} bytes | ok={ok} | challenge={challenge}", file=sys.stderr)
        if ok:
            return html
        last = html
        time.sleep(3 * (i + 1))
    return last  # best-effort; parser will return nothing and the source is skipped


def to_text(html):
    html = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.S)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html)


def iso_from_ddmmyy(s):
    """press '21/06/26' -> '2026-06-21' (YY is CE two-digit)."""
    m = re.match(r"(\d{2})/(\d{2})/(\d{2})$", s)
    if not m:
        return None
    d, mo, yy = int(m.group(1)), int(m.group(2)), int(m.group(3))
    year = 2000 + yy
    try:
        return datetime(year, mo, d).strftime("%Y-%m-%d")
    except ValueError:
        return None


def iso_from_thai(s):
    """ruayy '21 มิถุนายน 2569' -> '2026-06-21' (BE year)."""
    m = re.search(r"(\d{1,2})\s+([฀-๿]+)\s+(\d{4})", s)
    if not m:
        return None
    d, month_th, be = int(m.group(1)), m.group(2), int(m.group(3))
    mo = TH_MONTHS.get(month_th)
    if not mo:
        return None
    try:
        return datetime(be - 543, mo, d).strftime("%Y-%m-%d")
    except ValueError:
        return None


def fresh(iso, today):
    if not iso:
        return False
    try:
        dd = datetime.strptime(iso, "%Y-%m-%d").date()
    except ValueError:
        return False
    return 0 <= (today - dd).days <= MAX_STALE_DAYS


def parse_press(text):
    """Return {app_cat: {date,num4,top3,top2,bottom2}} from the latest table row."""
    out = {}
    for header, cat in PRESS_CATS.items():
        for m in re.finditer(re.escape(header), text):
            seg = text[m.start(): m.start() + 200]
            row = re.search(r"(\d{2}/\d{2}/\d{2})\s+(\d{4})\s+(\d{3})\s+(\d{2})\s+(\d{2})", seg)
            if row:
                out[cat] = {
                    "date": iso_from_ddmmyy(row.group(1)),
                    "num4": row.group(2),
                    "top3": row.group(3),
                    "top2": row.group(4),
                    "bottom2": row.group(5),
                }
                break
    return out


def parse_ruay(text):
    """ruayy: 'งวดวันที่ <thai date> 3 ตัวบน NNN 2 ตัวบน NN 2ตัวล่าง NN'."""
    m = re.search(
        r"งวดวันที่\s*(\d{1,2}\s+[฀-๿]+\s+\d{4})\s*3 ตัวบน\s*(\d{3})\s*2 ตัวบน\s*(\d{2})\s*2ตัวล่าง\s*(\d{2})",
        text,
    )
    if not m:
        return None
    return {
        "date": iso_from_thai(m.group(1)),
        "top3": m.group(2),
        "top2": m.group(3),
        "bottom2": m.group(4),
    }


HISTORY_LIMIT = 40  # ~1 month of daily draws; keep a little extra


def load_existing_history():
    """Per-category history dict from the previously-published JSON (or {}).
    Older JSON had no `history` key — seed it from the last `draws` so the
    previous latest result isn't dropped on the first history-enabled run."""
    try:
        with open("data/hanoi.json", encoding="utf-8") as f:
            prev = json.load(f)
    except Exception:
        return {}
    hist = prev.get("history")
    if hist:
        return hist
    seed = {}
    for cat, d in (prev.get("draws") or {}).items():
        if d and d.get("date"):
            seed[cat] = [d]
    return seed


def merge_history(existing, draws, limit=HISTORY_LIMIT):
    """Merge today's `draws` into existing per-category history: newest-first,
    dedup by date (today's value wins), trimmed to `limit`."""
    out = {}
    for cat in set(existing) | set(draws):
        by_date = {}
        for d in existing.get(cat, []):
            if d.get("date"):
                by_date[d["date"]] = d
        d = draws.get(cat)
        if d and d.get("date"):
            by_date[d["date"]] = d
        out[cat] = sorted(by_date.values(), key=lambda x: x["date"], reverse=True)[:limit]
    return out


def main():
    today = datetime.now(timezone(timedelta(hours=7))).date()  # Thai time
    draws = {}

    # press.in.th (primary) ----------------------------------------------------
    press = {}
    try:
        press = parse_press(to_text(fetch(PRESS_URL)))
    except Exception as e:
        print(f"[warn] press failed: {e}", file=sys.stderr)

    # ruayy (cross-check special, sole source for VIP) -------------------------
    ruay = {}
    for cat, url in RUAY.items():
        try:
            ruay[cat] = parse_ruay(to_text(fetch(url)))
        except Exception as e:
            print(f"[warn] ruayy {cat} failed: {e}", file=sys.stderr)

    # vn_hanoi_special: prefer press 4-digit, cross-check ruayy 3top/2bot ------
    p = press.get("vn_hanoi_special")
    r = ruay.get("vn_hanoi_special")
    if p and fresh(p["date"], today):
        verified = bool(r and r["top3"] == p["top3"] and r["bottom2"] == p["bottom2"] and r["date"] == p["date"])
        draws["vn_hanoi_special"] = {**p, "sources": ["press"] + (["ruayy"] if r else []), "verified": verified}

    # vn_hanoi (normal): press only, already validated == XSMB ------------------
    p = press.get("vn_hanoi")
    if p and fresh(p["date"], today):
        draws["vn_hanoi"] = {**p, "sources": ["press"], "verified": True}

    # vn_hanoi_vip: ruayy only (single-source, no 4-digit) → verified=false -----
    r = ruay.get("vn_hanoi_vip")
    if r and fresh(r["date"], today):
        draws["vn_hanoi_vip"] = {
            "date": r["date"], "num4": None,
            "top3": r["top3"], "top2": r["top2"], "bottom2": r["bottom2"],
            "sources": ["ruayy"], "verified": False,
        }

    # Guard FIRST: never overwrite the last good JSON with an empty result
    # (a transient parse miss must not wipe draws/history the app relies on).
    if not draws:
        print("[error] no fresh draws parsed from any source", file=sys.stderr)
        sys.exit(1)

    # Accumulate per-category history (the sources expose only today's draw, so
    # history grows one run at a time). `draws` (latest) stays unchanged for
    # backward compat; `history` is additive — old app versions ignore it.
    history = merge_history(load_existing_history(), draws)

    payload = {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "draws": draws,
        "history": history,
    }
    with open("data/hanoi.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
