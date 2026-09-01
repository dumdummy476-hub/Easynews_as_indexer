from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any

from .cache import SingleFlight, TTLCache
from .client import EasynewsClient
from .config import Settings
from .mapping import dedupe, map_results, sort_items
from .metadata import TmdbResolver
from .metrics import METRICS, SearchTimer
from .posted import map_posted_nzbs


class SearchService:
    def __init__(self, settings: Settings, client: EasynewsClient | None = None):
        self.settings = settings
        self.client = client or EasynewsClient(settings.easynews_user, settings.easynews_pass, settings)
        self.search_cache: TTLCache[list[dict[str, Any]]] = TTLCache(settings.cache_max_entries)
        self.posted_cache: TTLCache[dict[str, Any]] = TTLCache(settings.cache_max_entries)
        self.tmdb = TmdbResolver(settings)
        self.singleflight = SingleFlight()
        self._ready = False
        self._ready_lock = threading.Lock()

    def validate(self) -> None:
        self.settings.validate_runtime(); self.client.login()
        with self._ready_lock: self._ready = True

    @property
    def ready(self) -> bool:
        with self._ready_lock: return self._ready

    def _deadline(self) -> float:
        return time.monotonic() + self.settings.search_budget_ms / 1000.0

    @staticmethod
    def _within(deadline: float) -> bool:
        return time.monotonic() < deadline

    def _search_once(self, query: str, deadline: float, file_type: str = "VIDEO", per_page: int = 250) -> dict[str, Any]:
        result = self.client.search(query=query, file_type=file_type, per_page=per_page, sort_field="relevance", sort_dir="-", deadline=deadline)
        with self._ready_lock: self._ready = True
        return result

    @staticmethod
    def _cache_key(params: dict[str, Any]) -> str:
        raw = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str).encode()
        return hashlib.sha256(raw).hexdigest()

    def _map(self, data: dict[str, Any], *, min_bytes: int, query: str, year: int | None, season: int | None,
             episode: int | None, categories: set[int], strict: bool, maxage: int | None) -> list[dict[str, Any]]:
        return map_results(data, min_bytes, query, year, season, episode, categories, strict, maxage)

    def search(self, *, kind: str, query: str, year: int | None = None, season: int | None = None,
               episode: int | None = None, imdbid: str | None = None, tvdbid: str | None = None,
               categories: set[int] | None = None, min_size_mb: int | None = None, maxage: int | None = None,
               strict: bool = False, sort_mode: str | None = None) -> list[dict[str, Any]]:
        categories = categories or set()
        min_size_mb = self.settings.default_min_size_mb if min_size_mb is None else max(0, min_size_mb)
        sort_mode = (sort_mode or self.settings.sort_mode).lower()
        if sort_mode not in {"relevance", "quality", "size", "date"}: sort_mode = self.settings.sort_mode
        params = dict(kind=kind, query=query, year=year, season=season, episode=episode, imdbid=imdbid, tvdbid=tvdbid,
                      categories=sorted(categories), min_size_mb=min_size_mb, maxage=maxage, strict=strict, sort=sort_mode)
        key = self._cache_key(params); cached = self.search_cache.get(key)
        if cached is not None:
            METRICS.inc("search_cache_hits_total"); return cached

        def perform() -> list[dict[str, Any]]:
            second = self.search_cache.get(key)
            if second is not None:
                METRICS.inc("search_cache_hits_total"); return second
            with SearchTimer():
                METRICS.inc("searches_total"); deadline = self._deadline(); base_query = (query or "").strip(); tmdb = None
                if kind == "movie" and not base_query and imdbid:
                    tmdb = self.tmdb.find(imdbid if imdbid.startswith("tt") else f"tt{imdbid}", "imdb_id")
                    base_query = (tmdb or {}).get("display") or (tmdb or {}).get("original") or imdbid
                elif kind == "tv" and not base_query and tvdbid:
                    tmdb = self.tmdb.find(tvdbid, "tvdb_id")
                    base_query = (tmdb or {}).get("display") or (tmdb or {}).get("original") or tvdbid
                parts = [base_query]
                if kind == "movie" and year and str(year) not in base_query: parts.append(str(year))
                if kind == "tv":
                    if season is not None and episode is not None: parts.append(f"S{season:02}E{episode:02}")
                    elif season is not None: parts.append(f"S{season:02}")
                actual_query = " ".join(x for x in parts if x).strip()
                if not actual_query: return []
                min_bytes = min_size_mb * 1024 * 1024
                items = self._map(self._search_once(actual_query, deadline), min_bytes=min_bytes, query=actual_query,
                                  year=year, season=season, episode=episode, categories=categories, strict=strict, maxage=maxage)
                if kind == "movie" and year and len(items) < self.settings.title_retry_trigger and self._within(deadline):
                    METRICS.inc("title_retries_total")
                    items.extend(self._map(self._search_once(base_query, deadline), min_bytes=min_bytes, query=base_query,
                                           year=year, season=season, episode=episode, categories=categories, strict=strict, maxage=maxage))
                if kind == "movie" and self.settings.tmdb_enabled and len(items) < self.settings.tmdb_trigger and self._within(deadline):
                    if tmdb is None and imdbid: tmdb = self.tmdb.find(imdbid if imdbid.startswith("tt") else f"tt{imdbid}", "imdb_id")
                    original = (tmdb or {}).get("original")
                    if original and original.casefold() != base_query.casefold():
                        METRICS.inc("tmdb_retries_total")
                        items.extend(self._map(self._search_once(original, deadline), min_bytes=min_bytes, query=original,
                                               year=year, season=season, episode=episode, categories=categories, strict=strict, maxage=maxage))
                if self.settings.posted_nzb_enabled and len(items) < self.settings.posted_nzb_trigger and self._within(deadline):
                    METRICS.inc("posted_nzb_fallback_total"); broad_query = base_query if kind == "movie" else actual_query
                    broad = self._search_once(broad_query, deadline, file_type="ALL", per_page=200)
                    items.extend(map_posted_nzbs(self.client, self.posted_cache, self.settings, broad_query, broad, min_bytes,
                                                  deadline, year, season, episode, categories, strict))
                final = sort_items(dedupe(items), sort_mode); self.search_cache.set(key, final, self.settings.search_cache_ttl); return final
        return self.singleflight.run(key, perform)

    def get_posted_nzb(self, mid: str, mids: list[str] | None = None) -> bytes:
        cached = self.posted_cache.get(mid)
        if cached is not None and isinstance(cached.get("content"), (bytes, bytearray)):
            METRICS.inc("posted_nzb_cache_hits_total"); return bytes(cached["content"])
        info = self.client.inspect_posted_nzb(mid, mids); self.posted_cache.set(mid, info, self.settings.posted_nzb_cache_ttl)
        return bytes(info["content"])
