#!/usr/bin/env python3
"""
tv_storage_report.py — rank Sonarr-managed TV shows by disk usage, with
optional Plex "last watched" enrichment, to inform manual delete decisions.

Read-only: only issues GET requests. Never deletes or modifies anything.

Usage:
    export SONARR_URL="http://localhost:8989"      # or the container's LAN address
    export SONARR_API_KEY="..."                     # see STRATEGY.md for how to fetch this
    export PLEX_URL="http://localhost:32400"        # optional
    export PLEX_TOKEN="..."                          # optional
    export PLEX_TV_SECTION_ID="2"                    # optional, Plex library section id

    python3 tv_storage_report.py [--top 25] [--csv report.csv] [--stale-days 180]

Run this on/against the NAS (e.g. via `ssh schoerro-agent`) — Sonarr's API
is only reachable on the LAN, not from a Cowork session.
"""

import argparse
import csv
import os
import sys
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:
    sys.exit("Missing dependency: pip3 install requests --break-system-packages")


def fetch_sonarr_series(base_url: str, api_key: str) -> list[dict]:
    resp = requests.get(
        f"{base_url.rstrip('/')}/api/v3/series",
        headers={"X-Api-Key": api_key},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_plex_last_viewed(base_url: str, token: str, section_id: str) -> dict[str, datetime]:
    """Best-effort title -> last-viewed-at map from a Plex TV library section.

    Unverified against a live Plex instance — Plex's show-level `lastViewedAt`
    field availability varies by server version. Falls back to an empty dict
    (i.e. every show shows up as "N/A" for last-watched) on any failure rather
    than crashing the whole report.
    """
    try:
        resp = requests.get(
            f"{base_url.rstrip('/')}/library/sections/{section_id}/all",
            params={"type": 2, "X-Plex-Token": token},
            headers={"Accept": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("MediaContainer", {}).get("Metadata", [])
        out = {}
        for item in items:
            title = item.get("title")
            last_viewed = item.get("lastViewedAt")
            if title and last_viewed:
                out[title] = datetime.fromtimestamp(int(last_viewed), tz=timezone.utc)
        return out
    except Exception as e:
        print(f"  (Plex enrichment skipped: {e})", file=sys.stderr)
        return {}


def gb(bytes_val: float) -> float:
    return round(bytes_val / (1024 ** 3), 2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("SONARR_URL"), help="Sonarr base URL")
    parser.add_argument("--api-key", default=os.environ.get("SONARR_API_KEY"), help="Sonarr API key")
    parser.add_argument("--plex-url", default=os.environ.get("PLEX_URL"))
    parser.add_argument("--plex-token", default=os.environ.get("PLEX_TOKEN"))
    parser.add_argument("--plex-section", default=os.environ.get("PLEX_TV_SECTION_ID"))
    parser.add_argument("--top", type=int, default=25, help="Rows to print in the main table")
    parser.add_argument("--stale-days", type=int, default=180, help="Threshold for 'not watched recently'")
    parser.add_argument("--csv", help="Optional path to write the full ranked table as CSV")
    args = parser.parse_args()

    if not args.url or not args.api_key:
        sys.exit("Need --url/--api-key or SONARR_URL/SONARR_API_KEY env vars. See STRATEGY.md.")

    series = fetch_sonarr_series(args.url, args.api_key)

    last_viewed_by_title = {}
    if args.plex_url and args.plex_token and args.plex_section:
        last_viewed_by_title = fetch_plex_last_viewed(args.plex_url, args.plex_token, args.plex_section)
    else:
        print("  (Plex enrichment not configured — last-watched will show N/A for every show)", file=sys.stderr)

    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(days=args.stale_days)

    rows = []
    for s in series:
        stats = s.get("statistics", {})
        size_bytes = stats.get("sizeOnDisk", 0)
        title = s.get("title", "?")
        last_viewed = last_viewed_by_title.get(title)
        rows.append({
            "title": title,
            "size_gb": gb(size_bytes),
            "size_bytes": size_bytes,
            "episodes": f"{stats.get('episodeFileCount', 0)}/{stats.get('episodeCount', 0)}",
            "status": s.get("status", "?"),
            "monitored": s.get("monitored", False),
            "last_watched": last_viewed.date().isoformat() if last_viewed else "N/A",
            "stale": bool(last_viewed and last_viewed < stale_cutoff) if last_viewed else None,
        })

    rows.sort(key=lambda r: r["size_bytes"], reverse=True)

    total_gb = round(sum(r["size_gb"] for r in rows), 1)
    print(f"\n{len(rows)} shows, {total_gb} GB total\n")

    header = f"{'Show':<40} {'Size (GB)':>10} {'Episodes':>10} {'Status':>10} {'Monitored':>10} {'Last watched':>13}"
    print(header)
    print("-" * len(header))
    for r in rows[: args.top]:
        print(f"{r['title'][:40]:<40} {r['size_gb']:>10} {r['episodes']:>10} {r['status']:>10} "
              f"{str(r['monitored']):>10} {r['last_watched']:>13}")

    # "Start here" candidates: ended, unmonitored, and (if we have Plex data) stale.
    candidates = [
        r for r in rows
        if r["status"] == "ended" and not r["monitored"]
        and (r["stale"] is not False)  # True, or None (no Plex data — don't exclude on this alone)
    ]
    candidates.sort(key=lambda r: r["size_bytes"], reverse=True)

    if candidates:
        print(f"\nCandidates worth reviewing first (ended + unmonitored"
              f"{' + not watched in ' + str(args.stale_days) + 'd' if last_viewed_by_title else ''}):\n")
        for r in candidates:
            print(f"  {r['title'][:40]:<40} {r['size_gb']:>10} GB   last watched: {r['last_watched']}")
    else:
        print("\nNo shows matched the 'ended + unmonitored' candidate filter.")

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
            writer.writeheader()
            writer.writerows(rows)
        print(f"\nFull table written to {args.csv}")


if __name__ == "__main__":
    main()
