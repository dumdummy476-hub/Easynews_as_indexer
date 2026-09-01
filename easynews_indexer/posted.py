from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .cache import TTLCache
from .categories import category_matches
from .client import EasynewsClient, EasynewsError
from .config import Settings
from .mapping import sanitize_phrase, strict_match, tokenize
from .metrics import METRICS
from .release import parse_release

logger = logging.getLogger(__name__)


def map_posted_nzbs(client: EasynewsClient, cache: TTLCache[dict[str, Any]], settings: Settings,
                    query: str, data: dict[str, Any], min_bytes: int, deadline: float,
                    year: int | None, season: int | None, episode: int | None,
                    requested_categories: set[int], strict: bool) -> list[dict[str, Any]]:
    candidates = []
    for raw in data.get("data") or []:
        if not isinstance(raw, dict): continue
        ext = str(raw.get("extension") or raw.get("ext") or "").lower()
        if ext and not ext.startswith("."): ext = "." + ext
        mid = str(raw.get("mid") or "").strip()
        if ext == ".nzb" and mid:
            candidates.append(raw)
        if len(candidates) >= settings.posted_nzb_max: break
    if not candidates: return []
    inspected = {}
    def inspect(index: int, item: dict[str, Any]):
        mid = str(item.get("mid")); cached = cache.get(mid)
        if cached is not None:
            METRICS.inc("posted_nzb_cache_hits_total"); return index, cached
        if time.monotonic() >= deadline:
            raise EasynewsError("Search budget exhausted before posted NZB inspection")
        siblings = item.get("mids") if isinstance(item.get("mids"), list) else None
        info = client.inspect_posted_nzb(mid, siblings); cache.set(mid, info, settings.posted_nzb_cache_ttl)
        return index, info
    with ThreadPoolExecutor(max_workers=min(settings.posted_nzb_concurrency, len(candidates))) as pool:
        futures = {pool.submit(inspect, idx, item): idx for idx, item in enumerate(candidates)}
        for future in as_completed(futures):
            try:
                idx, info = future.result(); inspected[idx] = info
            except Exception as exc:
                METRICS.inc("posted_nzb_failures_total"); logger.warning("Posted NZB inspection failed: %s", exc)
    wanted = set(tokenize(query)); phrase = sanitize_phrase(query) if strict else None; output = []
    for idx, raw in enumerate(candidates):
        info = inspected.get(idx)
        if not info or int(info.get("size") or 0) < min_bytes: continue
        title = ".".join(str(raw.get("fn") or raw.get("filename") or "").replace(" - ", "-").split())
        rel = parse_release(title)
        if wanted and not wanted.issubset(set(tokenize(title))): continue
        if strict and not strict_match(title, phrase): continue
        if year and rel.year and rel.year != year: continue
        if season is not None and rel.season is not None and rel.season != season: continue
        if episode is not None and rel.episode is not None and rel.episode != episode: continue
        cats = [rel.category, *rel.extra_categories]
        if requested_categories and not any(category_matches(c, requested_categories) for c in cats): continue
        output.append({
            "hash": str(raw.get("hash") or raw.get("id") or raw.get("mid")), "filename": title, "ext": ".nzb",
            "sig": raw.get("sig"), "size": int(info.get("size") or 0), "title": title,
            "poster": raw.get("poster"), "posted": raw.get("timestamp") or raw.get("date"),
            "quality": rel.resolution, "category": rel.category, "categories": cats, "year": rel.year,
            "season": rel.season, "episode": rel.episode, "source_type": rel.source,
            "video_codec": rel.video_codec, "audio_codec": rel.audio, "hdr": rel.hdr,
            "quality_score": rel.quality_score, "source": "posted_nzb", "mid": raw.get("mid"), "mids": raw.get("mids"),
        })
    return output
