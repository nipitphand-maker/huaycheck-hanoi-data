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
import random
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CTX = ssl.create_default_context()

# Rotated per attempt: a fixed UA is an easy soft-block signature.
UAS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
]
TIMEOUT = 25
# Accept a draw only if its date is within this many days of the run — kills the
# "site shows yesterday, we stamp it today" failure mode.
MAX_STALE_DAYS = 3
# If the *published* data ever falls older than this, the scrape has been failing
# for real (not a one-off blip) — fail loudly so the workflow goes red and we
# find out in a day, not after a week of silent staleness.
MAX_DATA_AGE_DAYS = 1


def _direct(u):
    return u


def _via_allorigins(u):
    return "https://api.allorigins.win/raw?url=" + urllib.parse.quote(u, safe="")


def _via_codetabs(u):
    return "https://api.codetabs.com/v1/proxy/?quest=" + urllib.parse.quote(u, safe="")


# When the runner IP gets soft-blocked, re-fetch through a raw-HTML mirror on a
# *different* network. These return the ORIGINAL html, so the parsers below work
# unchanged. Tried in order, only after a direct hit fails.
EGRESSES = [("direct", _direct), ("allorigins", _via_allorigins), ("codetabs", _via_codetabs)]

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


def _fetch_once(url, ua):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "th,en-US;q=0.8,en;q=0.6",
            "Accept-Encoding": "identity",
            "Referer": "https://www.google.com/",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "no-cache",
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL_CTX) as r:
        return r.read().decode("utf-8", "ignore")


def fetch(url, must_contain="งวดวันที่", attempts=2):
    """Fetch a result page, defeating intermittent datacenter soft-blocks.

    Datacenter IPs sometimes get a soft-block page (HTTP 200, no real data)
    instead of the result table. We try the URL directly first, then re-fetch it
    through raw-HTML mirrors on a *different* network if the runner IP is blocked
    — rotating UA and backing off with jitter between attempts.

    Returns (html, ok): ok=True means a real result page came back (marker
    present, not a challenge). ok=False means every egress was blocked or errored
    — the caller treats that source as *unreachable*, not broken, so a transient
    block doesn't masquerade as a genuine layout change."""
    last = ""
    ua_i = 0
    for egress, build in EGRESSES:
        target = build(url)
        for i in range(attempts):
            ua = UAS[ua_i % len(UAS)]
            ua_i += 1
            try:
                html = _fetch_once(target, ua)
            except Exception as e:
                print(f"[diag] {url} [{egress}] attempt {i + 1}: {e}", file=sys.stderr)
                time.sleep(2 + 2 * i + random.uniform(0, 1.5))
                continue
            low = html.lower()
            challenge = any(x in low for x in ("just a moment", "cf-challenge", "cf_chl", "enable javascript and cookies", "attention required"))
            ok = (must_contain in html) and not challenge
            print(f"[diag] {url} [{egress}] attempt {i + 1} -> {len(html)} bytes | ok={ok} | challenge={challenge}", file=sys.stderr)
            if ok:
                return html, True
            last = html
            time.sleep(1 + i + random.uniform(0, 1.0))
    return last, False  # best-effort; every egress unreachable (likely soft-blocked)


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


def published_newest_date():
    """Newest draw date already committed in data/hanoi.json (None if missing/
    empty). Used to tell a one-off blip from data that has gone genuinely stale."""
    try:
        with open("data/hanoi.json", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    dates = []
    for d in (data.get("draws") or {}).values():
        try:
            dates.append(datetime.strptime(d["date"], "%Y-%m-%d").date())
        except (KeyError, TypeError, ValueError):
            continue
    return max(dates) if dates else None


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


def main():
    today = datetime.now(timezone(timedelta(hours=7))).date()  # Thai time
    draws = {}
    # Did *any* source serve a real result page this run? Distinguishes a
    # transient soft-block (every source unreachable) from a real layout change
    # (page fetched fine, but parsing produced nothing).
    any_reachable = False

    # press.in.th (primary) ----------------------------------------------------
    press = {}
    try:
        html, ok = fetch(PRESS_URL)
        any_reachable = any_reachable or ok
        press = parse_press(to_text(html))
    except Exception as e:
        print(f"[warn] press failed: {e}", file=sys.stderr)

    # ruayy (cross-check special, sole source for VIP) -------------------------
    ruay = {}
    for cat, url in RUAY.items():
        try:
            html, ok = fetch(url)
            any_reachable = any_reachable or ok
            ruay[cat] = parse_ruay(to_text(html))
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

    # Write fresh data only when we actually have some — never overwrite good
    # data/hanoi.json with {} on a bad run.
    if draws:
        payload = {
            "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "draws": draws,
        }
        with open("data/hanoi.json", "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif any_reachable:
        # Pages loaded fine but nothing parsed — a genuine layout change that
        # needs a code fix. Fail loudly right away.
        print("[error] sources reachable but no fresh draws parsed — "
              "page layout likely changed, fix the parser", file=sys.stderr)
        sys.exit(1)
    else:
        # Every egress was soft-blocked this run. Leave the last good data in
        # place; a single blip is tolerated (see the staleness alarm below).
        print("[warn] all sources unreachable (likely transient soft-block); "
              "keeping previous data for this run", file=sys.stderr)

    # Staleness alarm — the real guard against silently breaking for days. The
    # multiple daily crons make one blocked run a non-event, but if the PUBLISHED
    # data ever falls older than MAX_DATA_AGE_DAYS the scrape is failing for real,
    # so go red and surface it now instead of after a week.
    newest = published_newest_date()
    if newest is None:
        print("[error] no draws have ever been published — scrape is broken", file=sys.stderr)
        sys.exit(1)
    age = (today - newest).days
    if age > MAX_DATA_AGE_DAYS:
        print(f"[error] published data is {age} days stale (newest draw {newest}); "
              "the scrape has been failing across runs — needs attention", file=sys.stderr)
        sys.exit(1)
    print(f"[ok] published data current as of {newest} ({age}d old)", file=sys.stderr)


if __name__ == "__main__":
    main()
