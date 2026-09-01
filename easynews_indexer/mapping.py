from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Any

from .categories import category_matches
from .release import VIDEO_EXTS, parse_release

TOKEN_SPLIT = re.compile(r"[^\w]+", re.UNICODE)
STOPWORDS = {"the", "a", "an", "and", "of", "in", "for", "on"}


def tokenize(text: str) -> list[str]:
    return [x for x in TOKEN_SPLIT.sub(" ", (text or "").lower()).split() if len(x) > 1 and x not in STOPWORDS]


def sanitize_phrase(text: str) -> str:
    return re.sub(r"[^\w]+", " ", html.unescape(text or "").lower()).strip()


def strict_match(title: str, phrase: str | None) -> bool:
    if not phrase:
        return True
    cand = sanitize_phrase(title)
    needle = phrase.split()
    hay = cand.split()
    if cand == phrase:
        return True
    if not needle:
        return True

    # Newznab TV/movie queries can carry structural tokens separated by
    # metadata in release names. Example: "Silo S03E04" should match
    # "Silo.2023.S03E04..." even though the year appears in between.
    # Require the requested tokens in order rather than contiguously so
    # strict mode still rejects re-ordered/noisy titles without dropping
    # valid releases that insert a year or other metadata token.
    pos = 0
    for token in hay:
        if token == needle[pos]:
            pos += 1
            if pos == len(needle):
                return True
    return False


def parse_duration(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    text = str(value).strip().lower()
    if text.isdigit():
        return int(text)
    if ":" in text:
        try:
            nums = [int(x) for x in text.split(":")]
            if len(nums) == 3:
                return nums[0]*3600 + nums[1]*60 + nums[2]
            if len(nums) == 2:
                return nums[0]*60 + nums[1]
        except ValueError:
            return None
    total = 0
    found = False
    for suffix, mult in (("h", 3600), ("m", 60), ("s", 1)):
        m = re.search(rf"(\d+)\s*{suffix}", text)
        if m:
            total += int(m.group(1))*mult; found = True
    return total if found else None


def _coerce_size(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip().replace(",", "")
    try:
        return int(text)
    except ValueError:
        pass
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([kmgt]?i?b)", text, re.I)
    if not match:
        return 0
    number = float(match.group(1)); unit = match.group(2).lower()
    powers = {"b": 0, "kb": 1, "kib": 1, "mb": 2, "mib": 2, "gb": 3, "gib": 3, "tb": 4, "tib": 4}
    return int(number * (1024 ** powers[unit]))


def map_results(data: dict[str, Any], min_bytes: int, query: str = "", year: int | None = None,
                season: int | None = None, episode: int | None = None, requested_categories: set[int] | None = None,
                strict: bool = False, maxage: int | None = None) -> list[dict[str, Any]]:
    requested_categories = requested_categories or set()
    query_tokens = set(tokenize(query))
    phrase = sanitize_phrase(query) if strict else None
    now = datetime.now(timezone.utc).timestamp()
    output: list[dict[str, Any]] = []
    for raw in data.get("data") or []:
        if isinstance(raw, list):
            if len(raw) < 12:
                continue
            hash_id = raw[0]; size = _coerce_size(raw[4] if len(raw) > 4 else 0); subject = raw[6]
            poster = raw[7] if len(raw) > 7 else None; posted = raw[8] if len(raw) > 8 else None
            filename = raw[10]; ext = raw[11]; duration = raw[14] if len(raw) > 14 else None
            sig = None; meta = {}
        elif isinstance(raw, dict):
            hash_id = raw.get("hash") or raw.get("0") or raw.get("id")
            size = _coerce_size(raw.get("rawSize") or raw.get("size") or raw.get("4") or 0)
            subject = raw.get("subject") or raw.get("6") or ""
            poster = raw.get("poster") or raw.get("7")
            posted = raw.get("ts") or raw.get("timestamp") or raw.get("date") or raw.get("5")
            filename = raw.get("fn") or raw.get("filename") or raw.get("10") or ""
            ext = raw.get("extension") or raw.get("ext") or raw.get("11") or ""
            duration = raw.get("runtime") or raw.get("duration") or raw.get("14")
            sig = raw.get("sig")
            meta = {
                "resolution": raw.get("fullres") or raw.get("resolution") or (f"{raw.get('xres')}x{raw.get('yres')}" if raw.get("xres") and raw.get("yres") else None),
                "video_codec": raw.get("vcodec"), "audio_codec": raw.get("acodec"),
                "audio_languages": raw.get("audio_tracks") or raw.get("alangs") or raw.get("alang"),
                "subtitle_languages": raw.get("subtitle_tracks") or raw.get("slangs") or raw.get("slang"),
                "bitrate": raw.get("bps"),
            }
            if raw.get("passwd") or raw.get("password") or raw.get("virus"):
                continue
        else:
            continue
        ext_text = str(ext or "")
        if ext_text and not ext_text.startswith("."):
            ext_text = "." + ext_text
        if not hash_id or ext_text.lower() not in VIDEO_EXTS or size < min_bytes:
            continue
        duration_s = parse_duration(duration)
        if duration_s is not None and duration_s < 60:
            continue
        title = str(filename or subject or "").strip()
        if filename:
            title = ".".join(str(filename).replace(" - ", "-").split())
            if not title.lower().endswith(ext_text.lower()):
                title += ext_text
        title = html.unescape(title)
        info = parse_release(title, meta)
        if year and info.year and info.year != year:
            continue
        if season is not None and info.season is not None and info.season != season:
            continue
        if episode is not None and info.episode is not None and info.episode != episode:
            continue
        if strict and not strict_match(title, phrase):
            continue
        title_tokens = set(tokenize(title))
        if query_tokens and not query_tokens.issubset(title_tokens):
            continue
        all_categories = [info.category, *info.extra_categories]
        if requested_categories and not any(category_matches(category, requested_categories) for category in all_categories):
            continue
        if maxage is not None and posted:
            try:
                ts = float(posted)
                if ts > 10_000_000_000:
                    ts /= 1000.0
                if now - ts > maxage * 86400:
                    continue
            except (TypeError, ValueError):
                pass
        output.append({
            "hash": str(hash_id), "filename": str(filename), "ext": ext_text, "sig": sig,
            "size": size, "title": title, "poster": poster, "posted": posted,
            "duration": duration_s, "quality": info.resolution, "category": info.category, "categories": all_categories,
            "year": info.year, "season": info.season, "episode": info.episode,
            "source_type": info.source, "video_codec": info.video_codec or meta.get("video_codec"),
            "audio_codec": info.audio or meta.get("audio_codec"), "hdr": info.hdr,
            "audio_languages": meta.get("audio_languages"), "subtitle_languages": meta.get("subtitle_languages"),
            "bitrate": meta.get("bitrate"), "release_group": info.group, "proper": info.proper,
            "quality_score": info.quality_score,
        })
    return output


def dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_hash: set[str] = set(); seen_title: set[str] = set(); out = []
    for item in items:
        h = str(item.get("hash") or "")
        title = re.sub(r"\.(mkv|mp4|avi|mov|wmv|m2ts|ts|nzb)$", "", str(item.get("title") or ""), flags=re.I)
        title_key = re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()
        if h and h in seen_hash:
            continue
        if title_key and title_key in seen_title:
            continue
        if h:
            seen_hash.add(h)
        if title_key:
            seen_title.add(title_key)
        out.append(item)
    return out


def sort_items(items: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    if mode == "quality":
        return sorted(items, key=lambda x: (int(x.get("quality_score") or 0), int(x.get("size") or 0)), reverse=True)
    if mode == "size":
        return sorted(items, key=lambda x: int(x.get("size") or 0), reverse=True)
    if mode == "date":
        def ts(item: dict[str, Any]) -> float:
            try: return float(item.get("posted") or 0)
            except (TypeError, ValueError): return 0.0
        return sorted(items, key=ts, reverse=True)
    return items
