from __future__ import annotations

import logging

import requests

from .cache import TTLCache
from .config import Settings
from .metrics import METRICS

logger = logging.getLogger(__name__)


class TmdbResolver:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.cache: TTLCache[dict[str, str] | None] = TTLCache(settings.cache_max_entries)

    def find(self, external_id: str, source: str) -> dict[str, str] | None:
        if not self.settings.tmdb_enabled or not self.settings.tmdb_api_key or not external_id:
            return None
        key = f"{source}:{external_id}"
        cached = self.cache.get(key)
        if cached is not None:
            METRICS.inc("tmdb_cache_hits_total")
            return cached or None
        value = None
        try:
            response = requests.get(
                f"https://api.themoviedb.org/3/find/{external_id}",
                params={"api_key": self.settings.tmdb_api_key, "external_source": source, "language": "en-US"},
                timeout=(3, 5),
            )
            response.raise_for_status()
            payload = response.json()
            results = payload.get("movie_results") or [] if source == "imdb_id" else payload.get("tv_results") or []
            if results:
                row = results[0]
                if source == "imdb_id":
                    value = {"display": str(row.get("title") or "").strip(), "original": str(row.get("original_title") or "").strip()}
                else:
                    value = {"display": str(row.get("name") or "").strip(), "original": str(row.get("original_name") or "").strip()}
        except Exception as exc:
            logger.warning("TMDB resolution failed for %s: %s", external_id, exc)
            METRICS.inc("tmdb_errors_total")
        ttl = self.settings.tmdb_cache_ttl if value else self.settings.tmdb_negative_cache_ttl
        self.cache.set(key, value or {}, ttl)
        return value
