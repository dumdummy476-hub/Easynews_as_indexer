# Testing the hardening branch

Use branch `chatgpt/full-indexer-hardening`. Do not merge it until CI and the live smoke tests below pass.

## 1. Automated tests

```bash
python -m pip install -r requirements-dev.txt
ruff check .
pytest
```

CI also performs a Docker build smoke test.

## 2. Docker configuration

```bash
cp .env.example .env
nano .env
```

Set at least:

```env
EASYNEWS_USER=...
EASYNEWS_PASS=...
NEWZNAB_APIKEY=<long-random-secret>
EASYNEWS_SEARCH_API=v3
```

Then:

```bash
docker compose build --no-cache
docker compose up -d
docker compose ps
docker compose logs --tail=100 easynews_as_indexer
```

Expected: container is `healthy` and runs as the non-root `indexer` user.

## 3. Health and caps

```bash
curl -fsS http://127.0.0.1:8081/healthz
curl -fsS http://127.0.0.1:8081/readyz
curl -fsS "http://127.0.0.1:8081/api?t=caps&apikey=$NEWZNAB_APIKEY"
```

Confirm caps include Movies/SD `2030`, Movies/HD `2040`, Movies/UHD `2045`, TV/SD `5030`, TV/HD `5040`, and TV/UHD `5045`.

## 4. Search smoke tests

Movie:

```bash
curl -fsS "http://127.0.0.1:8081/api?t=movie&q=Blade%20Runner%202049&year=2017&cat=2045&limit=20&apikey=$NEWZNAB_APIKEY" > /tmp/movie.xml
```

TV:

```bash
curl -fsS "http://127.0.0.1:8081/api?t=tvsearch&q=The%20Last%20of%20Us&season=1&ep=1&cat=5000&limit=20&apikey=$NEWZNAB_APIKEY" > /tmp/tv.xml
```

Confirm the RSS contains `newznab:response`, correct categories and no unrelated titles.

## 5. Pagination/cache test

Run the same search twice and inspect `/metrics`:

```bash
curl -fsS "http://127.0.0.1:8081/api?t=movie&q=Matrix&year=1999&apikey=$NEWZNAB_APIKEY" >/dev/null
curl -fsS "http://127.0.0.1:8081/api?t=movie&q=Matrix&year=1999&apikey=$NEWZNAB_APIKEY" >/dev/null
curl -fsS http://127.0.0.1:8081/metrics | grep easynews_indexer_search_cache_hits_total
```

The second request should increment the cache hit counter and should not repeat the full Easynews search.

## 6. NZB grab validation

From one RSS item, use its `<link>` URL. The response must be valid NZB XML and must not be an Easynews HTML/login page. Tampering with any character in the `id=` token should return HTTP 400.

## 7. Prowlarr

Add a Generic Newznab indexer:

- URL: `http://easynews_as_indexer:8081/api` when Prowlarr shares the Docker network, otherwise the appropriate host URL.
- API Key: `NEWZNAB_APIKEY`.

Test the indexer, then manually search one movie, one TV episode, one UHD title and one HD title. Verify Prowlarr displays the expected categories.

## 8. Optional fallbacks

Only after the normal V3 path is stable, test TMDB and posted-NZB fallbacks separately. Watch `/metrics` and logs for fallback counts and NNTP validation failures.

## 9. Load sanity check

Issue several identical searches concurrently. Single-flight should coalesce them into one upstream search. Then issue different searches concurrently and confirm response latency remains below the configured `EASYNEWS_SEARCH_BUDGET_MS` under normal Easynews conditions.

## Rollback

The hardening work is isolated from `main`. If live testing is unsatisfactory, return to the existing main image/code; no database migration or persistent data conversion is involved.
