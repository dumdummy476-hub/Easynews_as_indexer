# [☕ Please support my work on Buy Me a Coffee](https://buymeacoffee.com/gaikwadsank)

# Easynews Newznab-like server

Flask server that bridges Easynews search to a Newznab-like API so you can add it to Prowlarr as a custom indexer and download NZBs. Video-only, sorts by relevance, returns as many results as possible, and filters files smaller than 100 MB.

## Setup (Local)

1. Create and activate a Python 3.11+ virtual environment:

```
# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1

# Linux / macOS (bash/zsh)
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```
pip install -r requirements.txt
```

3. Configure credentials and API key. Create a `.env` file in the repo root:

```
EASYNEWS_USER=your_easynews_username
EASYNEWS_PASS=your_easynews_password
NEWZNAB_APIKEY=testkey
```

4. Run the server:

```
python server.py
```

It starts on `http://127.0.0.1:8081`.

## Setup (Docker)


### Pull from GitHub Container Registry

```
docker pull ghcr.io/sanket9225/easynews_as_indexer:latest
```

Run the published image (Linux/macOS shells):

```
docker run --rm -d -p 8081:8081 \
	-e EASYNEWS_USER=your_easynews_username \
	-e EASYNEWS_PASS=your_easynews_password \
	-e NEWZNAB_APIKEY=testkey \
	-e PORT=8081 \
	-e STRICT_MATCHING=1 \
	ghcr.io/sanket9225/easynews_as_indexer:latest
```

> The published image currently includes `linux/amd64` and `linux/arm64` manifests.

Windows PowerShell equivalent:

```
docker run --rm -d -p 8081:8081 ^
	-e EASYNEWS_USER=your_easynews_username ^
	-e EASYNEWS_PASS=your_easynews_password ^
	-e NEWZNAB_APIKEY=testkey ^
	-e PORT=8081 ^
	-e STRICT_MATCHING=1 ^
	ghcr.io/sanket9225/easynews_as_indexer:latest
```

To tail logs from the detached container run `docker logs -f <container-id>`.

## Endpoints

- Caps: `GET /api?t=caps&apikey=<key>`
- Search (video-only): `GET /api?t=search&q=<query>&apikey=<key>&limit=<n>&minsize=<MB>`
	- Default `limit=100`, `minsize=100` (MB)
	- Also supports `t=movie` and `t=tvsearch`
	- **Strict matching** is enabled by default for `t=movie` and `t=tvsearch` (requires title to contain all query words); disabled for plain `t=search`
	- Optional `strict=0|1` overrides title matching strictness per request
	- Movie search accepts `year=<YYYY>` to bias results; TV search accepts `season=<NN>` and `ep=<NN>` (automatically appended as `SxxEyy` in the Easynews query)
- Download NZB: `GET /api?t=get&id=<encoded>&apikey=<key>`
	- Filename equals the item title

## Prowlarr integration

Add a Newznab (generic) indexer in Prowlarr:
- URL: `http://127.0.0.1:8081`
- API Key: the same key in your `.env` (e.g., `testkey`)

---

## [☕ If this project helps you, consider buying me a coffee](https://buymeacoffee.com/gaikwadsank)

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
