# Easynews as Indexer — hardened v2

A Newznab-compatible bridge that exposes Easynews Search API results to Prowlarr, Sonarr, Radarr and other Newznab clients.

## What v2 adds

- Easynews Search API V3 with parallel pagination and V2/auto fallback.
- Persistent bounded HTTP session pool with keep-alive and retry/backoff for 429/5xx responses.
- End-to-end search budget so optional fallbacks stop when a request is already too slow.
- Correct current Prowlarr/Newznab categories: Movies SD/HD/UHD = 2030/2040/2045 and TV SD/HD/UHD = 5030/5040/5045.
- Parent/child `cat=` filtering plus multiple category attributes (for example UHD + WEB-DL/x265).
- Newznab `<newznab:response offset="..." total="..."/>`, `limit`, `offset`, `minsize`, `maxsize`, `maxage`, and sorting.
- Short TTL search cache and single-flight request coalescing for simultaneous duplicate searches.
- Optional TMDB IMDb/TVDB title recovery with positive and negative caching.
- Optional posted-NZB fallback over Easynews NNTP with yEnc CRC/XML validation and short NZB-byte cache.
- Signed/HMAC result IDs instead of editable base64-only download IDs.
- Validated NZB downloads; HTML/login/error pages are rejected instead of being returned as NZBs.
- Rich release parsing for REMUX/BluRay/WEB-DL/WEBRip/HDTV, HEVC/AVC/AV1, DV/HDR10+, lossless/Atmos audio, PROPER/REPACK and release groups.
- `/healthz`, `/readyz` and Prometheus-style `/metrics` endpoints.
- Non-root, read-only Docker runtime and branch CI for compile, Ruff, pytest and Docker build.

## Quick start

```bash
cp .env.example .env
# edit .env and set EASYNEWS_USER, EASYNEWS_PASS and a strong NEWZNAB_APIKEY
docker compose build
docker compose up -d
```

Newznab URL inside the Docker network:

```text
http://easynews_as_indexer:8081/api
```

Host URL with the included compose file:

```text
http://HOST:8081/api
```

Use `NEWZNAB_APIKEY` as the API key in Prowlarr.

## Search modes

```env
EASYNEWS_SEARCH_API=v3   # default
EASYNEWS_SEARCH_API=v2
EASYNEWS_SEARCH_API=auto # V3 then V2 on V3 failure
```

V3 returns 100 rows per page. `EASYNEWS_V3_MAX_PAGES` and `EASYNEWS_V3_CONCURRENCY` bound the fan-out.

## Optional fallbacks

TMDB title recovery is disabled by default:

```env
EASYNEWS_TMDB_ORIGINAL_TITLE_FALLBACK=true
TMDB_API_KEY=...
```

Posted NZB inspection is also disabled by default because it adds NNTP work:

```env
EASYNEWS_V3_NZB_FALLBACK=true
```

For multipart posted NZB documents, the bridge assembles parts only when all sibling Message-IDs are supplied by the search result. Otherwise it fails closed rather than guessing article IDs.

## Security

`NEWZNAB_APIKEY=testkey` is no longer accepted as valid runtime configuration. Result IDs are HMAC-signed. Existing unsigned result IDs can be temporarily accepted with `ALLOW_LEGACY_UNSIGNED_IDS=true`, but the default is false.

## Observability

- `GET /healthz` — process health, no credential check.
- `GET /readyz` — configuration/upstream-validation state.
- `GET /metrics` — counters for searches, cache hits, upstream errors, fallbacks and latency. Set `METRICS_REQUIRE_APIKEY=true` if this endpoint is externally reachable.

See [TESTING.md](TESTING.md) for the pre-merge test checklist.
