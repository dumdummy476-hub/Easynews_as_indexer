from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _float(name: str, default: float, minimum: float | None = None) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


@dataclass(frozen=True)
class Settings:
    easynews_user: str
    easynews_pass: str
    api_key: str
    signing_secret: str
    search_api: str
    v3_max_pages: int
    v3_concurrency: int
    connect_timeout: float
    search_timeout: float
    download_timeout: float
    search_budget_ms: int
    search_cache_ttl: int
    cache_max_entries: int
    title_retry_trigger: int
    tmdb_trigger: int
    posted_nzb_trigger: int
    tmdb_enabled: bool
    tmdb_api_key: str
    tmdb_cache_ttl: int
    tmdb_negative_cache_ttl: int
    posted_nzb_enabled: bool
    posted_nzb_max: int
    posted_nzb_concurrency: int
    posted_nzb_cache_ttl: int
    nntp_host: str
    nntp_port: int
    nntp_timeout: float
    sort_mode: str
    default_limit: int
    max_limit: int
    default_min_size_mb: int
    allow_legacy_unsigned_ids: bool
    metrics_require_apikey: bool

    @classmethod
    def from_env(cls) -> "Settings":
        api_key = os.getenv("NEWZNAB_APIKEY", "").strip()
        signing = os.getenv("INDEXER_SIGNING_SECRET", "").strip() or api_key
        mode = os.getenv("EASYNEWS_SEARCH_API", "v3").strip().lower()
        if mode not in {"v2", "v3", "auto"}:
            mode = "v3"
        sort_mode = os.getenv("EASYNEWS_SORT_MODE", "relevance").strip().lower()
        if sort_mode not in {"relevance", "quality", "size", "date"}:
            sort_mode = "relevance"
        return cls(
            easynews_user=os.getenv("EASYNEWS_USER", "").strip(),
            easynews_pass=os.getenv("EASYNEWS_PASS", "").strip(),
            api_key=api_key,
            signing_secret=signing,
            search_api=mode,
            v3_max_pages=_int("EASYNEWS_V3_MAX_PAGES", 3, 1, 20),
            v3_concurrency=_int("EASYNEWS_V3_CONCURRENCY", 6, 1, 10),
            connect_timeout=_float("EASYNEWS_CONNECT_TIMEOUT", 5.0, 0.5),
            search_timeout=_float("EASYNEWS_SEARCH_TIMEOUT", 20.0, 1.0),
            download_timeout=_float("EASYNEWS_DOWNLOAD_TIMEOUT", 60.0, 1.0),
            search_budget_ms=_int("EASYNEWS_SEARCH_BUDGET_MS", 12000, 1000, 120000),
            search_cache_ttl=_int("EASYNEWS_SEARCH_CACHE_TTL", 45, 0, 3600),
            cache_max_entries=_int("EASYNEWS_CACHE_MAX_ENTRIES", 1024, 64, 100000),
            title_retry_trigger=_int("EASYNEWS_TITLE_RETRY_TRIGGER", _int("EASYNEWS_V3_NZB_TRIGGER", 20), 0, 1000),
            tmdb_trigger=_int("EASYNEWS_TMDB_TRIGGER", _int("EASYNEWS_V3_NZB_TRIGGER", 20), 0, 1000),
            posted_nzb_trigger=_int("EASYNEWS_POSTED_NZB_TRIGGER", _int("EASYNEWS_V3_NZB_TRIGGER", 20), 0, 1000),
            tmdb_enabled=_bool("EASYNEWS_TMDB_ORIGINAL_TITLE_FALLBACK", False),
            tmdb_api_key=os.getenv("TMDB_API_KEY", "").strip(),
            tmdb_cache_ttl=_int("EASYNEWS_TMDB_CACHE_TTL", 86400, 60, 604800),
            tmdb_negative_cache_ttl=_int("EASYNEWS_TMDB_NEGATIVE_CACHE_TTL", 900, 10, 86400),
            posted_nzb_enabled=_bool("EASYNEWS_V3_NZB_FALLBACK", False),
            posted_nzb_max=_int("EASYNEWS_POSTED_NZB_MAX", _int("EASYNEWS_V3_NZB_MAX", 8), 1, 20),
            posted_nzb_concurrency=_int("EASYNEWS_POSTED_NZB_CONCURRENCY", _int("EASYNEWS_V3_NZB_CONCURRENCY", 4), 1, 8),
            posted_nzb_cache_ttl=_int("EASYNEWS_POSTED_NZB_CACHE_TTL", 900, 10, 86400),
            nntp_host=os.getenv("EASYNEWS_NNTP_HOST", "news.easynews.com").strip(),
            nntp_port=_int("EASYNEWS_NNTP_PORT", 563, 1, 65535),
            nntp_timeout=_float("EASYNEWS_NNTP_TIMEOUT", 20.0, 1.0),
            sort_mode=sort_mode,
            default_limit=_int("NEWZNAB_DEFAULT_LIMIT", 100, 1, 300),
            max_limit=_int("NEWZNAB_MAX_LIMIT", 300, 1, 1000),
            default_min_size_mb=_int("NEWZNAB_DEFAULT_MIN_SIZE_MB", 100, 0, 100000),
            allow_legacy_unsigned_ids=_bool("ALLOW_LEGACY_UNSIGNED_IDS", False),
            metrics_require_apikey=_bool("METRICS_REQUIRE_APIKEY", False),
        )

    def validate_runtime(self) -> None:
        if not self.easynews_user or not self.easynews_pass:
            raise RuntimeError("EASYNEWS_USER and EASYNEWS_PASS are required")
        if not self.api_key or self.api_key == "testkey":
            raise RuntimeError("Set NEWZNAB_APIKEY to a non-default secret")
        if not self.signing_secret:
            raise RuntimeError("INDEXER_SIGNING_SECRET or NEWZNAB_APIKEY is required")
