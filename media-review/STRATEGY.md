# TV storage review strategy

Goal: find out which shows are eating the most disk space on schoerro, with enough context to make safe delete calls — not just a raw size ranking that ignores whether anyone still wants the show.

## Why Sonarr's API, not a filesystem `du` scan

Sonarr already tracks per-series disk usage internally (`statistics.sizeOnDisk` in its v3 API), computed from the episode files it manages. Pulling from there instead of running `du -sh` across the TV root folder means:

- No risk of double-counting hardlinks (qBittorrent → Sonarr hardlinking is a common setup and throws raw `du` off).
- The size ties directly to structured metadata (episode counts, monitored status, ended/continuing) in the same call, so the report is a ranking plus context, not just a ranking.
- No need to know or guess the exact host filesystem path for the TV library.

One API call (`GET /api/v3/series`) is enough. A raw filesystem scan is a reasonable fallback only if Sonarr's numbers look wrong or don't cover the whole library.

## Three signals, not just size

Size alone isn't "unnecessary" — a 40GB show you're mid-season on is not a deletion candidate; a 40GB show you finished 8 months ago is. The script below ranks by size but also surfaces, per show:

1. **Size on disk** — the headline ranking.
2. **Status/monitored** — `ended` + `monitored: false` means Sonarr isn't going to fetch anything else for it; that's a structural signal it's "done," independent of whether it's been watched.
3. **Last watched (optional, Plex)** — if a Plex token is supplied, the script does a best-effort title match against your Plex TV library and pulls `lastViewedAt`. This part is unverified against your real Plex instance (I have no network path to test it) — treat it as a starting point to refine, not a finished feature. Title matching is exact-ish, not fuzzy; shows that don't match will just show `N/A` and fall back to the size+status signals.

The script prints the full sorted table, then a separate "candidates worth reviewing first" section: ended, unmonitored, and (if Plex data is available) not watched in 180+ days — sorted by size within that filtered set. That's the actual "start here" list, not just top-N-by-size.

## Execution boundary

This needs to run against schoerro directly (Sonarr's API is only reachable on the NAS/LAN, and per the standing Cowork/Code split in this project, Cowork itself has no network route to schoerro). So the deliverable here is the script and the retrieval steps for its inputs — actually running it is a job for Claude Code (`ssh schoerro-agent`) or you running it directly.

**Getting the Sonarr API key** (needed as an env var, not hardcoded):
```
ssh schoerro-agent
sudo /usr/local/bin/docker exec sonarr grep -oP '(?<=<ApiKey>).*(?=</ApiKey>)' /config/config.xml
```
(Same pattern works for Radarr/Lidarr if you ever want the same report for movies/music — the script's `--url`/`--api-key` flags aren't Sonarr-specific.)

**Plex token** (optional): Settings → General → Network in Plex, or see Plex's own "Finding an authentication token" support doc. Section ID: visible in the URL when browsing to the TV library in Plex Web (`.../section/<ID>`).

## Safety

The script is read-only — it only does GETs against Sonarr/Plex and writes a report file. It does not delete, unmonitor, or modify anything. Deletion should stay a manual, reviewed step: once you've picked shows off the "candidates" list, delete via Sonarr itself (Series → Delete, with the "delete files from disk" option) rather than deleting files directly on the filesystem — that keeps Sonarr's own database in sync and avoids orphaned entries that redownload on the next RSS sync.
