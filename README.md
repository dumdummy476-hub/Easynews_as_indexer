
## Easynews Search API

This fork supports Easynews Search API V3 and the legacy V2 API.

### V3 - default

```env
EASYNEWS_SEARCH_API=v3
EASYNEWS_V3_MAX_PAGES=3
EASYNEWS_V3_CONCURRENCY=10
```

V3 returns up to 100 results per page. Additional pages are fetched in parallel, merged, and deduplicated before being returned through Newznab.

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
