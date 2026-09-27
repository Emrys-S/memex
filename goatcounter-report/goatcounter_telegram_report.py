#!/usr/bin/env python3
"""
Weekly GoatCounter stats -> Telegram message.

Fetches the last 7 days of stats for your site and sends a summary to
you via a Telegram bot. Intended to run via cron on your own
machine/NAS (not the sandbox that wrote this — GoatCounter and
Telegram's API aren't reachable from there).

Configuration is via environment variables so the API token and bot
token never need to be hardcoded in this file or committed anywhere.

Usage:
    python3 goatcounter_telegram_report.py

Test it manually first (see setup notes) before adding to cron. If any
API call fails, this prints the raw response so you can see exactly
what went wrong rather than failing silently.
"""

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone

# ---- Config (all from environment variables) -------------------------

GOATCOUNTER_CODE = os.environ.get("GOATCOUNTER_CODE", "emrys")
GOATCOUNTER_TOKEN = os.environ["GOATCOUNTER_API_TOKEN"]  # required

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]  # required
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]  # required

GOATCOUNTER_BASE = f"https://{GOATCOUNTER_CODE}.goatcounter.com/api/v0"
TELEGRAM_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def _esc(text):
    """Escape characters that break Telegram's legacy Markdown (an unescaped _ or * in a
    page path or referrer name makes sendMessage fail with 'can't parse entities')."""
    return re.sub(r"([_*`\[])", r"\\\1", str(text))


def api_get(path, params=None):
    """GET a GoatCounter API endpoint, returning parsed JSON. Raises
    with the raw response body on any non-200 so failures are visible."""
    url = f"{GOATCOUNTER_BASE}{path}"
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{query}"

    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {GOATCOUNTER_TOKEN}"}
    )
    # One retry on 404/5xx: GoatCounter returned a single transient 404 HTML page during
    # setup (2026-09-20) that succeeded on every later call.
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            print(f"ERROR calling {url}\nHTTP {e.code}: {body}", file=sys.stderr)
            if attempt == 1 and (e.code == 404 or e.code >= 500):
                time.sleep(3)
                continue
            raise


def format_report(total, hits, refs):
    lines = []
    lines.append(f"*GoatCounter weekly report*")
    lines.append(f"{GOATCOUNTER_CODE}.goatcounter.com — last 7 days\n")

    # GoatCounter's /stats/total has no "unique visitors" field; its `total` is documented as
    # "Total number of visitors (including events)", so report that as visitors.
    visitors = total.get("total", "?") if isinstance(total, dict) else "?"
    lines.append(f"Visitors: {visitors}\n")

    lines.append("*Top pages:*")
    hit_list = hits.get("hits", []) if isinstance(hits, dict) else []
    if not hit_list:
        lines.append("(no data)")
    for h in hit_list[:8]:
        path = h.get("path", "?")
        count = h.get("count", "?")
        lines.append(f"{count} — {_esc(path)}")

    ref_list = refs.get("stats", []) if isinstance(refs, dict) else []
    if ref_list:
        lines.append("\n*Top referrers:*")
        for r in ref_list[:5]:
            name = r.get("name") or "(direct / unknown)"  # GoatCounter uses "" for direct traffic
            count = r.get("count", "?")
            lines.append(f"{count} — {_esc(name)}")

    return "\n".join(lines)


def send_telegram(text):
    url = f"{TELEGRAM_BASE}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    }).encode("utf-8")

    req = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if not result.get("ok"):
                print(f"Telegram API returned an error: {result}", file=sys.stderr)
                raise RuntimeError(result)
            return result
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"ERROR calling Telegram sendMessage\nHTTP {e.code}: {body}", file=sys.stderr)
        raise


def main():
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=7)
    params = {
        "start": start.strftime("%Y-%m-%d"),
        "end": end.strftime("%Y-%m-%d"),
    }

    total = api_get("/stats/total", params)
    hits = api_get("/stats/hits", {**params, "limit": 8})

    try:
        refs = api_get("/stats/toprefs", params)
    except Exception:
        refs = {}  # not all plans/versions expose this; degrade gracefully

    body = format_report(total, hits, refs)
    print(body)  # always print, so cron logs capture it too

    send_telegram(body)
    print("\nSent to Telegram chat", TELEGRAM_CHAT_ID)


if __name__ == "__main__":
    main()
