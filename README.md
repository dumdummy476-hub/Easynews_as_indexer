
## Easynews Search API

This fork supports Easynews Search API V3 and the legacy V2 API.

### V3 - default

```env
EASYNEWS_SEARCH_API=v3
EASYNEWS_V3_MAX_PAGES=5
EASYNEWS_V3_CONCURRENCY=10
```

V3 returns up to 100 results per page. Additional pages are fetched in parallel, merged, and deduplicated before being returned through Newznab. `EASYNEWS_V3_MAX_PAGES` bounds how many pages (up to Easynews' own `numPages`) are fetched per search — raise it for more coverage on broad queries at the cost of more upstream requests.

### V2

```env
EASYNEWS_SEARCH_API=v2
```

Uses the legacy Easynews 2.0 search API with up to 250 results per request.

### Automatic fallback

```env
EASYNEWS_SEARCH_API=auto
```

Auto mode tries V3 first and falls back to V2 if V3 is unavailable. The same fallback applies during credential validation.

### Getting more results out of weak searches

These are off by default because they add latency to already-weak searches; enable them if recall matters more than search speed.

```env
# Resolve TMDB's canonical/original title for IMDb-only movie requests and
# TVDB-only TV requests, and retry with that title when the initial
# Easynews search returns few results (helps foreign-language and
# retitled releases). Requires TMDB_API_KEY.
EASYNEWS_TMDB_ORIGINAL_TITLE_FALLBACK=false
TMDB_API_KEY=

# When a search returns fewer than EASYNEWS_V3_NZB_TRIGGER results, run one
# broad Easynews V3 search for posted .nzb documents and validate up to
# EASYNEWS_V3_NZB_MAX candidates over NNTP.
EASYNEWS_V3_NZB_FALLBACK=false
EASYNEWS_V3_NZB_TRIGGER=20
EASYNEWS_V3_NZB_MAX=8
EASYNEWS_V3_NZB_CONCURRENCY=4
```

Movie searches already retry without the release year and with TMDB's original title when weak. TV searches get the same season-pack and original-title retries.

## V3 metadata

The bridge preserves resolution, video codec, audio codec, audio languages, subtitle languages, runtime, file size, and release date when supplied by Easynews V3.

Malformed bracketed title suffixes caused by Easynews encoding issues are cleaned for display only. The original filename used for NZB creation is preserved.

## Direct Newznab usage

Example Docker-network URL:

```text
http://easynews-indexer:8015/api
```

Use the value configured in NEWZNAB_APIKEY as the API key.

## NZB generation

V3 search results remain compatible with the Easynews /2.0/api/dl-nzb endpoint for NZB creation.
