from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import categories as cat

SEASON_EP_RE = re.compile(r"(?:s(?P<s>\d{1,2})e(?P<e>\d{1,3})|(?<!\d)(?P<s2>\d{1,2})x(?P<e2>\d{1,3})(?!\d))", re.I)
YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
RES_RE = re.compile(r"(?<!\d)(2160|1080|720|576|480)[pi]?(?!\d)", re.I)
VIDEO_EXTS = {".mkv", ".mp4", ".m4v", ".avi", ".ts", ".mov", ".wmv", ".mpg", ".mpeg", ".webm", ".m2ts"}
KNOWN_ANIME_GROUPS = {"subsplease", "erai-raws", "horriblesubs", "judas", "ember", "asw", "mtbb", "reinforce"}


@dataclass
class ReleaseInfo:
    title: str
    resolution: str | None = None
    source: str | None = None
    video_codec: str | None = None
    audio: str | None = None
    hdr: list[str] = field(default_factory=list)
    proper: str | None = None
    season: int | None = None
    episode: int | None = None
    year: int | None = None
    group: str | None = None
    category: int = cat.MOVIES
    extra_categories: list[int] = field(default_factory=list)
    quality_score: int = 0


def _has(text: str, pattern: str) -> bool:
    return re.search(pattern, text, re.I) is not None


def parse_release(title: str, metadata: dict[str, Any] | None = None) -> ReleaseInfo:
    metadata = metadata or {}
    t = title or ""
    lower = t.lower()
    info = ReleaseInfo(title=t)
    m = SEASON_EP_RE.search(t)
    if m:
        info.season = int(m.group("s") or m.group("s2"))
        info.episode = int(m.group("e") or m.group("e2"))

    # Remove the file extension and trailing release-group token before
    # looking for a movie year. Group names can legitimately contain a
    # year-like suffix (for example EDGE2020), which must not override the
    # actual release year in "Blade.Runner.2049.2017...-EDGE2020.mkv".
    year_text = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", t)
    year_text = re.sub(r"-[A-Za-z0-9][A-Za-z0-9._-]{1,30}$", "", year_text)
    year_matches = list(YEAR_RE.finditer(year_text))
    if year_matches:
        # A title itself can contain a year-like number (e.g. Blade Runner
        # 2049), so after excluding the release group prefer the last
        # remaining year-like token, which follows normal release naming.
        info.year = int(year_matches[-1].group(1))

    raw_res = str(metadata.get("resolution") or metadata.get("resolution_raw") or "")
    if "3840" in raw_res or "2160" in lower or "4k" in lower or "uhd" in lower:
        info.resolution = "2160p"
    else:
        rm = RES_RE.search(t)
        if rm:
            info.resolution = f"{rm.group(1)}p"
    if _has(t, r"\bremux\b"):
        info.source = "REMUX"
    elif _has(t, r"\b(?:blu[ ._-]?ray|bdrip|bdremux)\b"):
        info.source = "BluRay"
    elif _has(t, r"\bweb[ ._-]?(?:dl|rip)\b"):
        info.source = "WEB-DL" if _has(t, r"web[ ._-]?dl") else "WEBRip"
    elif _has(t, r"\bhdtv\b"):
        info.source = "HDTV"
    if _has(t, r"\b(?:hevc|h[ ._-]?265|x265)\b") or str(metadata.get("video_codec", "")).lower() in {"hevc", "h265", "h.265"}:
        info.video_codec = "HEVC"
    elif _has(t, r"\b(?:avc|h[ ._-]?264|x264)\b"):
        info.video_codec = "AVC"
    elif _has(t, r"\bav1\b"):
        info.video_codec = "AV1"
    if _has(t, r"dolby[ ._-]?vision|\b(?:dv)\b"):
        info.hdr.append("DV")
    if _has(t, r"hdr10\+"):
        info.hdr.append("HDR10+")
    elif _has(t, r"\bhdr(?:10)?\b"):
        info.hdr.append("HDR10")
    if _has(t, r"truehd.*atmos|atmos.*truehd"):
        info.audio = "TrueHD Atmos"
    elif _has(t, r"dts[ ._-]?hd.*ma"):
        info.audio = "DTS-HD MA"
    elif _has(t, r"dts[ ._-]?x"):
        info.audio = "DTS:X"
    elif _has(t, r"(?:ddp|eac3|dd\+).*atmos|atmos.*(?:ddp|eac3|dd\+)"):
        info.audio = "DD+ Atmos"
    elif metadata.get("audio_codec"):
        info.audio = str(metadata["audio_codec"])
    if _has(t, r"\brepack\b"):
        info.proper = "REPACK"
    elif _has(t, r"\bproper\b"):
        info.proper = "PROPER"
    gm = re.search(r"-([A-Za-z0-9][A-Za-z0-9._-]{1,30})$", re.sub(r"\.[A-Za-z0-9]{2,5}$", "", t))
    if gm:
        info.group = gm.group(1)
    anime_group = re.match(r"^\[([^\]]+)\]", t)
    is_anime = bool(anime_group and anime_group.group(1).strip().lower() in KNOWN_ANIME_GROUPS and info.season is None)
    is_tv = info.season is not None or info.episode is not None
    if is_anime:
        info.category = cat.TV_ANIME
    elif is_tv:
        if info.resolution == "2160p":
            info.category = cat.TV_UHD
        elif info.resolution in {"1080p", "720p"}:
            info.category = cat.TV_HD
        else:
            info.category = cat.TV_SD
    else:
        if info.resolution == "2160p":
            info.category = cat.MOVIES_UHD
        elif info.resolution in {"1080p", "720p"}:
            info.category = cat.MOVIES_HD
        else:
            info.category = cat.MOVIES_SD
    if is_tv or is_anime:
        if info.source == "WEB-DL":
            info.extra_categories.append(cat.TV_WEBDL)
        if info.video_codec == "HEVC" or _has(t, r"\bx265\b"):
            info.extra_categories.append(cat.TV_X265)
    else:
        if info.source == "BluRay" or info.source == "REMUX":
            info.extra_categories.append(cat.MOVIES_BLURAY)
        if info.source == "WEB-DL":
            info.extra_categories.append(cat.MOVIES_WEBDL)
        if info.video_codec == "HEVC" or _has(t, r"\bx265\b"):
            info.extra_categories.append(cat.MOVIES_X265)
    info.extra_categories = list(dict.fromkeys(x for x in info.extra_categories if x != info.category))

    score = {"2160p": 40, "1080p": 25, "720p": 15}.get(info.resolution or "", 5)
    score += {"REMUX": 30, "BluRay": 20, "WEB-DL": 12, "WEBRip": 7, "HDTV": 4}.get(info.source or "", 0)
    score += 8 if "DV" in info.hdr else 0
    score += 5 if "HDR10+" in info.hdr else 0
    score += 4 if info.audio in {"TrueHD Atmos", "DTS-HD MA", "DTS:X"} else 0
    score += 2 if info.proper else 0
    info.quality_score = score
    return info
