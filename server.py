from concurrent.futures import ThreadPoolExecutor, as_completed
import base64
import html
import os
import re
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set
from urllib.parse import quote

import requests
from flask import Flask, Response, request
import json

from easynews_client import EasynewsClient, EasynewsError, SearchItem


APP = Flask(__name__)
_CLIENT: Optional[EasynewsClient] = None
_CLIENT_LOCK = threading.Lock()
_CLIENT_LOGIN_TTL = 600  # seconds
_CLIENT_LAST_LOGIN: float = 0.0


def _load_dotenv():
    path = os.path.join(os.getcwd(), ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    os.environ.setdefault(k, v)
    except Exception:
        pass


_load_dotenv()

API_KEY = os.environ.get("NEWZNAB_APIKEY", "testkey")
EZ_USER = os.environ.get("EASYNEWS_USER")
EZ_PASS = os.environ.get("EASYNEWS_PASS")


def require_apikey() -> bool:
    key = request.args.get("apikey") or request.headers.get("X-Api-Key")
    return (API_KEY is None) or (key == API_KEY)


def client() -> EasynewsClient:
    if not EZ_USER or not EZ_PASS:
        raise RuntimeError("Set EASYNEWS_USER and EASYNEWS_PASS environment variables")
    global _CLIENT, _CLIENT_LAST_LOGIN
    with _CLIENT_LOCK:
        now = time.time()
        if _CLIENT is None:
            _CLIENT = EasynewsClient(EZ_USER, EZ_PASS)
            _CLIENT.login()
            _CLIENT_LAST_LOGIN = now
        elif now - _CLIENT_LAST_LOGIN > _CLIENT_LOGIN_TTL:
            try:
                _CLIENT.login()
            except EasynewsError:
                _CLIENT = EasynewsClient(EZ_USER, EZ_PASS)
                _CLIENT.login()
            _CLIENT_LAST_LOGIN = time.time()
        return _CLIENT


def xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _normalize_imdb_id(value: Optional[str]) -> Optional[str]:
    """Normalize Newznab imdbid values such as 0079264 to tt0079264."""
    value = str(value or "").strip()

    if not value:
        return None

    if re.fullmatch(r"\d+", value):
        return f"tt{value}"

    if re.fullmatch(r"tt\d+", value, flags=re.IGNORECASE):
        return value.lower()

    return None


def _tmdb_movie_titles(
    imdb_value: Optional[str],
) -> Optional[Dict[str, str]]:
    """
    Resolve TMDB's English/display title and canonical original title.

    This remains intentionally conservative:
    - IMDb ID lookup only
    - no text search
    - no alternative-title fan-out
    - no translated-title guessing
    """
    api_key = os.environ.get("TMDB_API_KEY", "").strip()

    if not api_key:
        return None

    imdb_id = _normalize_imdb_id(imdb_value)

    if not imdb_id:
        return None

    response = requests.get(
        f"https://api.themoviedb.org/3/find/{imdb_id}",
        params={
            "api_key": api_key,
            "external_source": "imdb_id",
            "language": "en-US",
        },
        timeout=5,
    )
    response.raise_for_status()

    payload = response.json()
    movies = payload.get("movie_results") or []

    if not movies:
        return None

    movie = movies[0]

    title = str(
        movie.get("title") or ""
    ).strip()

    original_title = str(
        movie.get("original_title") or ""
    ).strip()

    if not title and not original_title:
        return None

    return {
        "title": title or original_title,
        "original_title": original_title or title,
    }


def _tmdb_tv_titles_from_tvdb(
    tvdb_value: Optional[str],
) -> Optional[Dict[str, str]]:
    """
    Resolve a TVDB series ID through TMDB and return both the
    English/display name and canonical original name.
    """
    api_key = os.environ.get("TMDB_API_KEY", "").strip()

    if not api_key:
        return None

    tvdb_id = str(tvdb_value or "").strip()

    if not re.fullmatch(r"\d+", tvdb_id):
        return None

    response = requests.get(
        f"https://api.themoviedb.org/3/find/{tvdb_id}",
        params={
            "api_key": api_key,
            "external_source": "tvdb_id",
            "language": "en-US",
        },
        timeout=5,
    )
    response.raise_for_status()

    payload = response.json()
    shows = payload.get("tv_results") or []

    if not shows:
        return None

    show = shows[0]

    name = str(
        show.get("name") or ""
    ).strip()

    original_name = str(
        show.get("original_name") or ""
    ).strip()

    if not name and not original_name:
        return None

    return {
        "name": name or original_name,
        "original_name": original_name or name,
    }


def _tmdb_original_title(imdb_value: Optional[str]) -> Optional[str]:
    """Compatibility helper returning only TMDB's original title."""
    titles = _tmdb_movie_titles(imdb_value)

    if not titles:
        return None

    return titles.get("original_title") or None


def _release_merge_key(item: dict) -> str:
    """Generate the same title-based dedupe key for merged search sources."""
    title = str(
        item.get("title") or ""
    ).strip().lower()

    title = re.sub(
        r"\.(mkv|mp4|avi|mov|wmv|m2ts|ts|nzb)$",
        "",
        title,
        flags=re.IGNORECASE,
    )

    return re.sub(
        r"[^a-z0-9]+",
        " ",
        title,
    ).strip()


def encode_id(item: dict) -> str:
    # Pack info needed to build NZB for a single selection and preserve title for filename
    payload = {
        "hash": item.get("hash"),
        "filename": item.get("filename"),
        "ext": item.get("ext"),
        "sig": item.get("sig"),
        "title": item.get("title"),
    }

    # Posted NZB documents discovered through Easynews V3 are fetched
    # directly from Easynews NNTP instead of being regenerated by
    # the legacy /2.0/api/dl-nzb endpoint.
    if item.get("source"):
        payload["source"] = item.get("source")

    if item.get("mid"):
        payload["mid"] = item.get("mid")
    if item.get("sample"):
        payload["sample"] = True
    raw = (
        base64.urlsafe_b64encode(json.dumps(payload, ensure_ascii=False).encode())
        .decode()
        .rstrip("=")
    )
    return raw


def decode_id(enc: str) -> dict:
    pad = "=" * (-len(enc) % 4)
    raw = base64.urlsafe_b64decode(enc + pad).decode()
    return json.loads(raw)


def to_search_item(d: dict) -> SearchItem:
    return SearchItem(
        id=None,
        hash=d["hash"],
        filename=d["filename"],
        ext=d["ext"],
        sig=d.get("sig"),
        type="VIDEO",
        raw={},
    )


_TITLE_PARENS_RE = re.compile(r"\(([^()]*)\)")


def _normalize_title(raw: str) -> str:
    text = html.unescape(raw or "").strip()
    if not text:
        return text
    matches = _TITLE_PARENS_RE.findall(text)
    for candidate in reversed(matches):
        cleaned = candidate.strip()
        if cleaned:
            return cleaned
    return text


def _coerce_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            try:
                return datetime.fromtimestamp(int(text), tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                dt = datetime.strptime(text.replace("Z", "+0000"), fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except ValueError:
                continue
    return None


_ALLOWED_VIDEO_EXTENSIONS = {
    ".mkv",
    ".mp4",
    ".m4v",
    ".avi",
    ".ts",
    ".mov",
    ".wmv",
    ".mpg",
    ".mpeg",
    ".flv",
    ".webm",
}

_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "of",
    "in",
    "for",
    "on",
}

_MIN_DURATION_SECONDS = 60
_TOKEN_SPLIT_RE = re.compile(r"[^\w]+", re.UNICODE)
_QUALITY_RE = re.compile(r"(2160|1440|1080|720|480|360)\s*(p|i)?", re.IGNORECASE)
_YEAR_RE = re.compile(r"(19|20)\d{2}")
_SEASON_EP_RE = re.compile(
    r"(?:s(?P<season>\d{1,2})e(?P<episode>\d{1,2})|(?<!\d)(?P<season2>\d{1,2})x(?P<episode2>\d{1,2})(?!\d))",
    re.IGNORECASE,
)
# Anime detection patterns
_ANIME_BRACKET_GROUP_RE = re.compile(r"^\[([^\]]+)\]", re.IGNORECASE)

# Known fansub groups for anime detection
_KNOWN_FANSUB_GROUPS = {
    "subsplease",
    "erai-raws",
    "horriblesubs",
    "judas",
    "gjm",
    "commiesubs",
    "commie",
    "animekaizoku",
    "anime time",
    "asenshi",
    "damedesuyo",
    "gg",
    "fff",
    "underwater",
    "ember",
    "kametsu",
    "kawaiika",
    "mezashite",
    "reinforce",
    "senritsu",
    "vivid",
    "coalgirls",
    "utw",
    "thora",
    "ohys-raws",
    "leopard-raws",
    "asw",
    "mtbb",
    "anime-time",
}
_SANITIZE_SYMBOLS_RE = re.compile(r"[\.\-_:\s]+")
_NON_ALNUM_RE = re.compile(r"[^\w\sÀ-ÿ]")

# Newznab category constants
CATEGORY_MOVIES = 2000
CATEGORY_MOVIES_HD = 2030
CATEGORY_MOVIES_UHD = 2040
CATEGORY_TV = 5000
CATEGORY_TV_HD = 5030
CATEGORY_TV_UHD = 5040
CATEGORY_ANIME = 5070  # Anime as TV subcategory
CATEGORY_OTHER = 7000


def _parse_duration_seconds(raw: Any) -> Optional[int]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        if raw <= 0:
            return None
        return int(raw)
    text = str(raw).strip().lower()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    total = 0
    matched = False
    for label, multiplier in (("h", 3600), ("m", 60), ("s", 1)):
        for part in re.findall(rf"(\d+)\s*{label}", text):
            total += int(part) * multiplier
            matched = True
    if matched:
        return total
    if ":" in text:
        try:
            pieces = [int(p) for p in text.split(":")]
            if len(pieces) == 3:
                h, m, s = pieces
            elif len(pieces) == 2:
                h = 0
                m, s = pieces
            else:
                return None
            return h * 3600 + m * 60 + s
        except ValueError:
            return None
    return None


def _as_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _tokenize(text: str) -> List[str]:
    if not text:
        return []
    normalized = _TOKEN_SPLIT_RE.sub(" ", text.lower())
    tokens = [
        tok for tok in normalized.split() if len(tok) > 1 and tok not in _STOPWORDS
    ]
    return tokens


def _sanitize_phrase(text: str) -> str:
    if not text:
        return ""
    working = text.replace("&", " and ")
    working = _SANITIZE_SYMBOLS_RE.sub(" ", working)
    working = _NON_ALNUM_RE.sub("", working)
    return working.lower().strip()


def _is_flagged_item(item: Any, ext: str, duration_seconds: Optional[int]) -> bool:
    passwd = False
    virus = False
    file_type = ""
    if isinstance(item, dict):
        passwd = bool(item.get("passwd") or item.get("password"))
        virus = bool(item.get("virus"))
        file_type = str(item.get("type") or item.get("file_type") or "").upper()
    if passwd or virus:
        return True
    if file_type and file_type != "VIDEO":
        return True
    if ext and ext.lower() not in _ALLOWED_VIDEO_EXTENSIONS:
        return True
    if duration_seconds is not None and duration_seconds < _MIN_DURATION_SECONDS:
        return True
    return False


def _format_duration(seconds: Optional[int]) -> Optional[str]:
    if seconds is None:
        return None
    if seconds <= 0:
        return None
    td = timedelta(seconds=seconds)
    total_seconds = int(td.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{secs:02}"


def _clean_mojibake_title(text: str) -> str:
    """
    Remove obviously corrupted bracketed suffixes returned by Easynews.

    Important:
    - Only affects the display/search title.
    - The original Easynews filename remains untouched for NZB generation.
    - Normal Unicode and legitimate bracketed release groups are preserved.
    """
    if not text:
        return text

    mojibake_markers = ("Ã", "Â", "\ufffd")

    def bad_bracket(match):
        content = match.group(1)

        marker_count = sum(content.count(x) for x in mojibake_markers)
        control_count = sum(
            1 for ch in content
            if 0x80 <= ord(ch) <= 0x9F
        )

        if marker_count >= 2 or control_count:
            return ""

        return match.group(0)

    cleaned = re.sub(r"\[([^\[\]]*)\]", bad_bracket, text)

    # Clean whitespace/dots left behind after removing a bad suffix.
    cleaned = re.sub(r"\.{2,}", ".", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+\.", ".", cleaned)
    cleaned = cleaned.strip(" ._-")

    return cleaned


def _extract_quality(*texts: Optional[str]) -> Optional[str]:
    for text in texts:
        if not text:
            continue
        lowered = text.lower()
        if "4k" in lowered:
            return "2160p"
        match = _QUALITY_RE.search(lowered)
        if match:
            value = match.group(1)
            suffix = match.group(2) or "p"
            return f"{value}{suffix.lower()}"
        if "uhd" in lowered:
            return "2160p"
        if "fhd" in lowered:
            return "1080p"
    return None


def _build_thumbnail_url(
    base: Optional[str], hash_id: Optional[str], slug: Optional[str]
) -> Optional[str]:
    if not base or not hash_id:
        return None
    base = base.rstrip("/") + "/"
    prefix = hash_id[:3]
    safe_slug = quote((slug or hash_id).replace("/", "_"))
    return f"{base}{prefix}/pr-{hash_id}.jpg/th-{safe_slug}.jpg"


def _extract_release_markers(
    text: str, quality_hint: Optional[str] = None
) -> Dict[str, Optional[Any]]:
    info: Dict[str, Optional[Any]] = {}
    if not text:
        return info
    season_match = _SEASON_EP_RE.search(text)
    if season_match:
        season = season_match.group("season") or season_match.group("season2")
        episode = season_match.group("episode") or season_match.group("episode2")
        if season:
            info["season"] = int(season)
        if episode:
            info["episode"] = int(episode)
    year_match = _YEAR_RE.search(text)
    if year_match:
        info["year"] = int(year_match.group(0))
    quality = quality_hint or _extract_quality(text)
    if quality:
        info["quality"] = quality
    return info


def _detect_anime(title: str) -> bool:
    """
    Detect anime releases using fansub indicators.

    Requirements (all must be true):
    1. Bracketed release group at start: [Group]
    2. Group must be in known fansub whitelist
    3. Episode-only numbering (- 01, Ep 01, Episode 01)
    4. NO traditional TV patterns (S01E01, 1x02)

    Returns: True if anime, False otherwise
    """
    # Guard: Exclude if traditional TV patterns exist
    if _SEASON_EP_RE.search(title):
        return False

    bracket_match = _ANIME_BRACKET_GROUP_RE.search(title)
    if not bracket_match:
        return False

    # This prevents [BBC], [PBS], [REPACK] from being detected as anime
    group_name = bracket_match.group(1).strip().lower()
    if group_name not in _KNOWN_FANSUB_GROUPS:
        return False

    # Remove bracketed group to avoid false matches
    title_without_group = title[bracket_match.end() :].strip()

    # Episode patterns: "- 01", "Ep 01", "Episode 01", "- 01v2", "-1090."
    # Support up to 4 digits for long-running anime (e.g., One Piece episode 1045)
    episode_patterns = [
        r"[\s\-_]+\d{1,4}(?:\s*v\d+)?[\s\-_\.\(\[]",  # " - 1090 " or "-1045." or "- 01v2 -"
        r"[\s\-_]Ep?\.?\s*\d{1,4}",  # "- Ep01" or " E1045"
        r"[\s\-_]Episode\s*\d{1,4}",  # "- Episode 01" or "- Episode 1045"
    ]

    has_episode = any(
        re.search(pattern, title_without_group, re.IGNORECASE)
        for pattern in episode_patterns
    )

    return has_episode


def _detect_category(title: str, metadata: Dict[str, Optional[Any]]) -> int:
    """
    Detect Newznab category based on filename and extracted metadata.

    Detection logic:
    1. Anime: bracketed fansub groups + episode-only patterns (PRIORITY)
    2. TV shows: presence of season/episode patterns (SxxExx or xxyy)
    3. Movies: presence of year, absence of TV patterns
    4. Resolution subcategories: 720p+ = HD, 2160p/4K/UHD = UHD (TV/Movies only)
    5. Default to generic categories if uncertain

    Args:
        title: The filename/title to analyze
        metadata: Dict with season, episode, year, quality keys

    Returns:
        Newznab category ID (int)
    """
    # Check for anime FIRST (priority detection)
    if _detect_anime(title):
        return CATEGORY_ANIME  # 5070 - No quality subcategories

    season = metadata.get("season")
    episode = metadata.get("episode")
    quality = metadata.get("quality")
    year = metadata.get("year")

    quality_lower = (quality or "").lower()
    is_uhd = False
    is_hd = False

    if quality_lower:
        # UHD: 2160p or higher, or contains 4k/uhd keywords
        if "2160" in quality_lower or "4k" in quality_lower or "uhd" in quality_lower:
            is_uhd = True
        # HD: 720p or 1080p
        elif "720" in quality_lower or "1080" in quality_lower:
            is_hd = True

    has_tv_pattern = season is not None or episode is not None

    if not has_tv_pattern:
        if _SEASON_EP_RE.search(title):
            has_tv_pattern = True

    if has_tv_pattern:
        if is_uhd:
            return CATEGORY_TV_UHD  # 5040
        elif is_hd:
            return CATEGORY_TV_HD  # 5030
        else:
            return CATEGORY_TV  # 5000

    # Movies typically have a year but no season/episode
    if year or (not has_tv_pattern):
        if is_uhd:
            return CATEGORY_MOVIES_UHD  # 2040
        elif is_hd:
            return CATEGORY_MOVIES_HD  # 2030
        else:
            return CATEGORY_MOVIES  # 2000

    # Default fallback to generic Movies
    return CATEGORY_MOVIES  # 2000


def _matches_strict(title: str, strict_phrase: Optional[str]) -> bool:
    if not strict_phrase:
        return True
    candidate = _sanitize_phrase(title)
    if not candidate:
        return False
    if candidate == strict_phrase:
        return True
    candidate_tokens = candidate.split()
    phrase_tokens = strict_phrase.split()
    if not phrase_tokens:
        return True
    for idx in range(0, max(1, len(candidate_tokens) - len(phrase_tokens) + 1)):
        if candidate_tokens[idx : idx + len(phrase_tokens)] == phrase_tokens:
            return True
    return False


def filter_and_map(
    json_data: dict,
    min_bytes: int,
    query_tokens: Optional[List[str]] = None,
    query_meta: Optional[Dict[str, Optional[Any]]] = None,
    strict_phrase: Optional[str] = None,
    strict_match: bool = False,
) -> List[dict]:
    token_set: Set[str] = set(query_tokens or [])
    thumb_base = json_data.get("thumbURL") or json_data.get("thumbUrl")
    out: List[dict] = []
    for it in json_data.get("data", []):
        hash_id: Optional[str] = None
        subject: Optional[str] = None
        filename_no_ext: Optional[str] = None
        ext: Optional[str] = None
        size: Any = 0
        poster: Optional[str] = None
        posted_raw: Any = None
        sig: Optional[str] = None
        display_fn: Optional[str] = None
        extension_field: Optional[str] = None
        duration_raw: Any = None
        fullres: Optional[str] = None

        # Easynews V3 rich media metadata
        resolution_raw: Optional[str] = None
        video_codec: Optional[str] = None
        audio_codec: Optional[str] = None
        audio_languages: Any = None
        subtitle_languages: Any = None
        bitrate: Any = None

        if isinstance(it, list):
            if len(it) >= 12:
                hash_id = it[0]
                subject = it[6]
                filename_no_ext = it[10]
                ext = it[11]
            if len(it) > 7:
                poster = it[7]
            if len(it) > 8:
                posted_raw = it[8]
            if len(it) > 14:
                duration_raw = it[14]
        elif isinstance(it, dict):
            hash_id = it.get("hash") or it.get("0") or it.get("id")
            subject = it.get("subject") or it.get("6")
            filename_no_ext = it.get("filename") or it.get("fn") or it.get("10")
            ext = it.get("ext") or it.get("extension") or it.get("11")
            size = it.get("size", 0)
            poster = it.get("poster") or it.get("7")
            posted_raw = it.get("timestamp") or it.get("ts") or it.get("dtime") or it.get("date") or it.get("12")
            sig = it.get("sig")
            display_fn = it.get("fn") or it.get("filename")
            extension_field = it.get("extension") or it.get("ext")
            duration_raw = it.get("14") or it.get("duration") or it.get("len")
            fullres = it.get("fullres") or it.get("resolution")

            # Easynews V3 media metadata
            xres = it.get("xres") or it.get("width")
            yres = it.get("yres") or it.get("height")

            if xres and yres:
                resolution_raw = f"{xres}x{yres}"
            elif fullres:
                resolution_raw = str(fullres)

            video_codec = it.get("vcodec")
            audio_codec = it.get("acodec")
            audio_languages = it.get("audio_tracks") or it.get("alang")
            subtitle_languages = it.get("subtitle_tracks") or it.get("slang")
            bitrate = it.get("bps")

        if not hash_id or not ext:
            continue

        filename_no_ext = filename_no_ext or ""
        ext = ext or ""
        if extension_field and not ext:
            ext = extension_field

        # Try to use numeric size if present; otherwise skip (can't verify <100MB rule)
        if not isinstance(size, int):
            try:
                size = int(size)
            except Exception:
                size = 0

        if size < min_bytes:
            continue

        duration_seconds = _parse_duration_seconds(duration_raw)

        if _is_flagged_item(it, ext, duration_seconds):
            continue

        title: Optional[str] = None
        if display_fn:
            cleaned = display_fn.strip()
            if cleaned:
                normalized = cleaned.replace(" - ", "-")
                parts = [segment for segment in normalized.split(" ") if segment]
                sanitized = ".".join(parts)
                ext_component = extension_field or ext or ""
                if ext_component and not ext_component.startswith("."):
                    ext_component = f".{ext_component}"
                title = f"{sanitized}{ext_component}" if ext_component else sanitized

        if not title:
            fallback = subject or f"{filename_no_ext}{ext}"
            title = _normalize_title(fallback)

        # Easynews occasionally returns multiply-encoded garbage inside
        # bracketed release-group suffixes. Clean display title only.
        title = _clean_mojibake_title(title)

        quality = _extract_quality(title, fullres)
        title_meta = _extract_release_markers(title, quality)
        if not quality and title_meta.get("quality"):
            quality = title_meta.get("quality")

        if strict_match and not _matches_strict(title, strict_phrase):
            continue

        if query_meta:
            q_year = query_meta.get("year")
            q_season = query_meta.get("season")
            q_episode = query_meta.get("episode")
            q_quality = query_meta.get("quality")
            t_year = title_meta.get("year")
            t_season = title_meta.get("season")
            t_episode = title_meta.get("episode")
            t_quality = quality or title_meta.get("quality")
            if q_year and t_year and q_year != t_year:
                continue
            if q_season and t_season and q_season != t_season:
                continue
            if q_episode and t_episode and q_episode != t_episode:
                continue
            if q_quality and t_quality and q_quality.lower() != t_quality.lower():
                continue

        if token_set:
            title_tokens = set(_tokenize(title))
            if not title_tokens or not token_set.issubset(title_tokens):
                continue

        duration_formatted = _format_duration(duration_seconds)
        thumbnail_url = _build_thumbnail_url(thumb_base, hash_id, filename_no_ext)
        year = title_meta.get("year")

        out.append(
            {
                "hash": hash_id,
                "filename": filename_no_ext,
                "ext": ext,
                "sig": sig,
                "size": size,
                "title": title,
                "poster": poster,
                "posted": posted_raw,
                "duration": duration_seconds,
                "duration_hms": duration_formatted,
                "quality": quality,
                "thumbnail": thumbnail_url,
                "year": year,
                "season": title_meta.get("season"),
                "episode": title_meta.get("episode"),

                # Easynews V3 metadata retained for Newznab output
                "resolution_raw": resolution_raw,
                "video_codec": video_codec,
                "audio_codec": audio_codec,
                "audio_languages": audio_languages,
                "subtitle_languages": subtitle_languages,
                "bitrate": bitrate,
            }
        )
    return out


def map_posted_nzbs(
    c: EasynewsClient,
    json_data: dict,
    min_bytes: int,
    query_tokens: Optional[List[str]] = None,
    query_meta: Optional[Dict[str, Optional[Any]]] = None,
    strict_phrase: Optional[str] = None,
    strict_match: bool = False,
    max_candidates: int = 8,
    concurrency: int = 4,
) -> List[dict]:
    """
    Convert posted .nzb documents found by a broad Easynews V3 search
    into validated release-level results.

    Each candidate NZB is fetched from Easynews NNTP, yEnc-decoded and
    XML-validated. Invalid/missing NZBs are skipped without affecting
    normal Easynews VIDEO results.
    """
    raw_candidates: List[dict] = []
    seen: Set[str] = set()

    for it in json_data.get("data", []):
        if not isinstance(it, dict):
            continue

        ext = str(
            it.get("extension")
            or it.get("ext")
            or ""
        ).strip().lower()

        if ext != ".nzb":
            continue

        mid = str(it.get("mid") or "").strip()
        if not mid:
            continue

        dedupe_key = str(
            it.get("hash")
            or mid
        )

        if dedupe_key in seen:
            continue

        seen.add(dedupe_key)
        raw_candidates.append(it)

        if len(raw_candidates) >= max(1, max_candidates):
            break

    if not raw_candidates:
        return []

    workers = max(
        1,
        min(
            int(concurrency or 1),
            len(raw_candidates),
            8,
        ),
    )

    inspected: Dict[int, Dict[str, Any]] = {}

    def inspect_one(index: int, item: dict):
        mid = str(item.get("mid") or "").strip()
        info = c.inspect_posted_nzb(mid)
        return index, info

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(inspect_one, idx, item): (idx, item)
            for idx, item in enumerate(raw_candidates)
        }

        for future in as_completed(futures):
            idx, item = futures[future]

            try:
                result_idx, info = future.result()
                inspected[result_idx] = info

            except Exception as e:
                APP.logger.warning(
                    "Skipping invalid posted NZB %r (%s): %s",
                    item.get("fn")
                    or item.get("filename")
                    or item.get("mid"),
                    item.get("mid"),
                    e,
                )

    token_set: Set[str] = set(query_tokens or [])
    out: List[dict] = []

    for idx, it in enumerate(raw_candidates):
        info = inspected.get(idx)

        if not info:
            continue

        size = int(info.get("size") or 0)

        if size < min_bytes:
            continue

        filename = str(
            it.get("fn")
            or it.get("filename")
            or ""
        ).strip()

        if not filename:
            continue

        # Match the normal bridge display style, but deliberately do not
        # append ".nzb" because this represents the underlying release.
        normalized = filename.replace(" - ", "-")
        parts = [
            segment
            for segment in normalized.split(" ")
            if segment
        ]
        title = ".".join(parts)

        title = _clean_mojibake_title(title)

        quality = _extract_quality(title, None)
        title_meta = _extract_release_markers(title, quality)

        if not quality and title_meta.get("quality"):
            quality = title_meta.get("quality")

        if strict_match and not _matches_strict(
            title,
            strict_phrase,
        ):
            continue

        if query_meta:
            q_year = query_meta.get("year")
            q_season = query_meta.get("season")
            q_episode = query_meta.get("episode")
            q_quality = query_meta.get("quality")

            t_year = title_meta.get("year")
            t_season = title_meta.get("season")
            t_episode = title_meta.get("episode")
            t_quality = quality or title_meta.get("quality")

            if q_year and t_year and q_year != t_year:
                continue

            if (
                q_season is not None
                and t_season is not None
                and q_season != t_season
            ):
                continue

            if (
                q_episode is not None
                and t_episode is not None
                and q_episode != t_episode
            ):
                continue

            if (
                q_quality
                and t_quality
                and q_quality.lower() != t_quality.lower()
            ):
                continue

        if token_set:
            title_tokens = set(_tokenize(title))

            if (
                not title_tokens
                or not token_set.issubset(title_tokens)
            ):
                continue

        posted_raw = (
            it.get("timestamp")
            or it.get("date")
        )

        out.append(
            {
                "hash": (
                    it.get("hash")
                    or it.get("id")
                    or it.get("mid")
                ),
                "filename": filename,
                "ext": ".nzb",
                "sig": it.get("sig"),
                "size": size,
                "title": title,
                "poster": it.get("poster"),
                "posted": posted_raw,
                "duration": None,
                "duration_hms": None,
                "quality": quality,
                "thumbnail": None,
                "year": title_meta.get("year"),
                "season": title_meta.get("season"),
                "episode": title_meta.get("episode"),

                "resolution_raw": None,
                "video_codec": None,
                "audio_codec": None,
                "audio_languages": None,
                "subtitle_languages": None,
                "bitrate": None,

                # Special GET routing.
                "source": "posted_nzb",
                "mid": it.get("mid"),

                # Useful internally/logging; harmless if ignored.
                "nzb_file_count": info.get("file_count"),
                "nzb_segment_count": info.get("segment_count"),
            }
        )

    return out



@APP.route("/api")
def api():
    if not require_apikey():
        return Response("Unauthorized", status=401)

    t = request.args.get("t", "caps")
    if t == "caps":
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<caps>"
            '<server version="0.1" title="Easynews Bridge"/>'
            '<limits max="300" default="100"/>'
            '<registration available="no" open="no"/>'
            "<searching>"
            '<search available="yes" supportedParams="q"/>'
            '<movie-search available="yes" supportedParams="q,year,imdbid"/>'
            '<tv-search available="yes" supportedParams="q,season,ep,tvdbid"/>'
            "</searching>"
            "<categories>"
            '<category id="2000" name="Movies">'
            '<subcat id="2030" name="Movies/HD"/>'
            '<subcat id="2040" name="Movies/UHD"/>'
            "</category>"
            '<category id="5000" name="TV">'
            '<subcat id="5030" name="TV/HD"/>'
            '<subcat id="5040" name="TV/UHD"/>'
            '<subcat id="5070" name="TV/Anime"/>'
            "</category>"
            '<category id="7000" name="Other"/>'
            "</categories>"
            "</caps>"
        )
        return Response(xml, mimetype="application/xml")

    if t in ("search", "movie", "tvsearch"):
        base_query = (request.args.get("q") or "").strip()
        cat_param = request.args.get("cat") or ""
        season_param = request.args.get("season") or request.args.get("seasonnum")
        episode_param = (
            request.args.get("ep")
            or request.args.get("epnum")
            or request.args.get("episode")
        )
        year_param = request.args.get("year") or request.args.get("yr")
        imdb_param = (
            request.args.get("imdbid")
            or request.args.get("imdb")
        )
        tvdb_param = (
            request.args.get("tvdbid")
            or request.args.get("tvdb")
        )

        tmdb_original_title_hint: Optional[str] = None
        tmdb_tv_original_name_hint: Optional[str] = None

        # AIOStreams prefers imdbid over q when the indexer advertises
        # IMDb-ID support. Resolve an IMDb-only movie request to TMDB's
        # canonical original_title before constructing the Easynews query.
        #
        # If TMDB is temporarily unavailable, use the normalized IMDb ID
        # as a harmless non-empty query instead of treating the request as
        # a Prowlarr validation call and returning a fake sample result.
        if t == "movie" and not base_query and imdb_param:
            tmdb_id_lookup_enabled = (
                os.environ.get(
                    "EASYNEWS_TMDB_ORIGINAL_TITLE_FALLBACK",
                    "false",
                ).strip().lower()
                in {"1", "true", "yes", "on"}
            )

            if tmdb_id_lookup_enabled:
                try:
                    resolved_titles = _tmdb_movie_titles(imdb_param)

                    if resolved_titles:
                        display_title = (
                            resolved_titles.get("title") or ""
                        ).strip()
                        tmdb_original_title_hint = (
                            resolved_titles.get("original_title") or ""
                        ).strip() or None

                        base_query = (
                            display_title
                            or tmdb_original_title_hint
                            or _normalize_imdb_id(imdb_param)
                            or str(imdb_param).strip()
                        )

                        APP.logger.info(
                            "TMDB IMDb-only movie lookup: %r -> "
                            "display=%r original=%r",
                            imdb_param,
                            base_query,
                            tmdb_original_title_hint,
                        )
                    else:
                        base_query = (
                            _normalize_imdb_id(imdb_param)
                            or str(imdb_param).strip()
                        )
                except Exception as e:
                    APP.logger.warning(
                        "TMDB IMDb-only movie lookup failed for %r: %s",
                        imdb_param,
                        e,
                    )
                    base_query = (
                        _normalize_imdb_id(imdb_param)
                        or str(imdb_param).strip()
                    )

        # TVDB-only TV search.
        #
        # Once tvdbid support is advertised, AIOStreams will prefer the
        # stable TVDB ID instead of sending q. Resolve that ID through
        # TMDB before constructing the Easynews title + SxxExx query.
        if t == "tvsearch" and not base_query and tvdb_param:
            tmdb_tv_lookup_enabled = (
                os.environ.get(
                    "EASYNEWS_TMDB_ORIGINAL_TITLE_FALLBACK",
                    "false",
                ).strip().lower()
                in {"1", "true", "yes", "on"}
            )

            if tmdb_tv_lookup_enabled:
                try:
                    resolved_tv = _tmdb_tv_titles_from_tvdb(tvdb_param)

                    if resolved_tv:
                        display_name = (
                            resolved_tv.get("name") or ""
                        ).strip()

                        tmdb_tv_original_name_hint = (
                            resolved_tv.get("original_name") or ""
                        ).strip() or None

                        base_query = (
                            display_name
                            or tmdb_tv_original_name_hint
                            or str(tvdb_param).strip()
                        )

                        APP.logger.info(
                            "TMDB TVDB-only lookup: %r -> "
                            "display=%r original=%r",
                            tvdb_param,
                            base_query,
                            tmdb_tv_original_name_hint,
                        )
                    else:
                        base_query = str(tvdb_param).strip()

                except Exception as e:
                    APP.logger.warning(
                        "TMDB TVDB-only lookup failed for %r: %s",
                        tvdb_param,
                        e,
                    )
                    base_query = str(tvdb_param).strip()
            else:
                base_query = str(tvdb_param).strip()

        season_int = _as_int(season_param)
        episode_int = _as_int(episode_param)
        year_int = _as_int(year_param)

        search_components: List[str] = []
        if base_query:
            search_components.append(base_query)

        if t == "movie":
            if year_int and str(year_int) not in base_query:
                search_components.append(str(year_int))
        elif t == "tvsearch":
            if season_int is not None and episode_int is not None:
                search_components.append(f"S{season_int:02}E{episode_int:02}")
            elif season_int is not None:
                search_components.append(f"S{season_int:02}")
            if year_int and str(year_int) not in base_query:
                search_components.append(str(year_int))

        search_label = " ".join(part for part in search_components if part).strip()
        raw_query = search_label or base_query
        q = raw_query.strip()
        fallback_query = False
        if (
            not q or q.lower() == "test"
        ):  # allow Prowlarr validation calls to receive data
            # Check if TV/Anime categories are requested
            tv_categories = {"5000", "5030", "5040"}
            anime_categories = {"5070"}
            requested_categories = set(cat_param.split(",")) if cat_param else set()
            wants_tv = t == "tvsearch" or bool(requested_categories & tv_categories)
            wants_anime = bool(requested_categories & anime_categories)
            # Use appropriate fallback query
            if wants_anime:
                q = "one piece"  # Anime fallback
            elif wants_tv:
                q = "breaking bad"  # TV fallback
            else:
                q = "matrix"  # Movie fallback
            fallback_query = True
        query_tokens = _tokenize(raw_query)
        query_meta = _extract_release_markers(raw_query)
        if year_int:
            query_meta["year"] = year_int
        if season_int is not None:
            query_meta["season"] = season_int
        if episode_int is not None:
            query_meta["episode"] = episode_int
        strict_param = request.args.get("strict")
        strict_requested = t in {"movie", "tvsearch"}
        if strict_param is not None:
            strict_requested = strict_param.strip().lower() not in {
                "0",
                "false",
                "no",
                "off",
            }
        strict_phrase = _sanitize_phrase(raw_query) if strict_requested else None
        limit = int(request.args.get("limit", "100"))
        offset = int(request.args.get("offset", "0"))
        min_size_param = request.args.get("minsize")
        min_size_mb = 100
        if min_size_param:
            try:
                min_size_mb = max(100, int(min_size_param))
            except ValueError:
                min_size_mb = 100
        min_bytes = min_size_mb * 1024 * 1024

        if fallback_query:
            # Check if TV/Anime categories are requested
            tv_categories = {"5000", "5030", "5040"}
            anime_categories = {"5070"}
            requested_categories = set(cat_param.split(",")) if cat_param else set()
            wants_tv = t == "tvsearch" or bool(requested_categories & tv_categories)
            wants_anime = bool(requested_categories & anime_categories)

            if wants_anime:
                # Anime-appropriate fallback
                items = [
                    {
                        "hash": "SAMPLEHASH_ANIME123",
                        "filename": "sample.anime.series.01.720p.mkv",
                        "ext": ".mkv",
                        "sig": None,
                        "size": 350 * 1024 * 1024,
                        "title": "[SampleSubs] Sample Anime Series - 01 [720p]",
                        "sample": True,
                        "poster": "sample@example.com",
                        "posted": int(time.time()),
                    }
                ]
            elif wants_tv:
                # TV-appropriate fallback for Sonarr
                items = [
                    {
                        "hash": "SAMPLEHASH_TV123456",
                        "filename": "sample.tv.show.s01e01.1080p.mkv",
                        "ext": ".mkv",
                        "sig": None,
                        "size": 800 * 1024 * 1024,
                        "title": "Sample TV Show S01E01 1080p",
                        "sample": True,
                        "poster": "sample@example.com",
                        "posted": int(time.time()),
                    }
                ]
            else:
                # Movie fallback for Radarr
                items = [
                    {
                        "hash": "SAMPLEHASH1234567890",
                        "filename": "sample.matrix.clip",
                        "ext": ".mkv",
                        "sig": None,
                        "size": 700 * 1024 * 1024,
                        "title": "Sample Matrix Clip",
                        "sample": True,
                        "poster": "sample@example.com",
                        "posted": int(time.time()),
                    }
                ]
        else:
            c = client()
            # aim for maximum results per page
            data = c.search(
                query=q,
                file_type="VIDEO",
                per_page=250,
                sort_field="relevance",
                sort_dir="-",
            )
            if fallback_query:
                items = filter_and_map(data, min_bytes=min_bytes)
            else:
                items = filter_and_map(
                    data,
                    min_bytes=min_bytes,
                    query_tokens=query_tokens,
                    query_meta=query_meta,
                    strict_phrase=strict_phrase,
                    strict_match=strict_requested,
                )

        # Movie title-only VIDEO retry.
        #
        # Easynews V3 can sometimes return dramatically fewer results when
        # the release year is appended to the search text. If the primary
        # "title + year" search is weak, retry the same display title without
        # the year while still enforcing the requested year via query_meta.
        try:
            title_only_trigger = max(
                0,
                int(
                    os.environ.get(
                        "EASYNEWS_V3_NZB_TRIGGER",
                        "20",
                    )
                ),
            )
        except ValueError:
            title_only_trigger = 20

        if (
            not fallback_query
            and t == "movie"
            and base_query
            and year_int
            and str(year_int) not in base_query
            and len(items) < title_only_trigger
        ):
            title_only_query = base_query.strip()
            title_only_tokens = _tokenize(title_only_query)

            title_only_strict_phrase = (
                _sanitize_phrase(title_only_query)
                if strict_requested
                else None
            )

            try:
                APP.logger.info(
                    "Easynews title-only VIDEO retry: %r -> %r "
                    "(results=%s, trigger=%s)",
                    q,
                    title_only_query,
                    len(items),
                    title_only_trigger,
                )

                # V3-only retry: deliberately bypass the legacy V2 path.
                title_only_data = c._search_v3(
                    query=title_only_query,
                    file_type="VIDEO",
                    per_page=250,
                    sort_field="relevance",
                    sort_dir="-",
                )

                # The generic release-marker parser may mistake a
                # four-digit number in the movie title itself for a year
                # (for example "Blade Runner 2049"). For this title-only
                # recovery pass, enforce all other metadata normally but
                # validate the requested movie year explicitly against the
                # resulting release title instead.
                title_only_meta = dict(query_meta)
                title_only_meta.pop("year", None)

                title_only_items = filter_and_map(
                    title_only_data,
                    min_bytes=min_bytes,
                    query_tokens=title_only_tokens,
                    query_meta=title_only_meta,
                    strict_phrase=title_only_strict_phrase,
                    strict_match=strict_requested,
                )

                if year_int:
                    requested_year_pattern = re.compile(
                        rf"(?<!\d){re.escape(str(year_int))}(?!\d)"
                    )

                    title_only_items = [
                        item
                        for item in title_only_items
                        if requested_year_pattern.search(
                            str(
                                item.get("title")
                                or item.get("filename")
                                or ""
                            )
                        )
                    ]

                existing_keys = {
                    _release_merge_key(item)
                    for item in items
                    if _release_merge_key(item)
                }

                title_only_added = 0

                for title_only_item in title_only_items:
                    key = _release_merge_key(title_only_item)

                    if key and key in existing_keys:
                        continue

                    items.append(title_only_item)

                    if key:
                        existing_keys.add(key)

                    title_only_added += 1

                APP.logger.info(
                    "Easynews title-only VIDEO retry added %s release(s)",
                    title_only_added,
                )

            except Exception as e:
                # This retry must never break the normal search path.
                APP.logger.warning(
                    "Easynews title-only VIDEO retry failed for %r: %s",
                    title_only_query,
                    e,
                )

        # Preserve the title/token set that the posted-NZB fallback
        # should use. If TMDB resolves a different original movie title,
        # these are switched to that title below.
        posted_base_query = base_query
        posted_query_tokens = query_tokens
        posted_strict_phrase = strict_phrase

        # Optional IMDb -> TMDB original-title fallback.
        #
        # AIOStreams sends imdbid for movie requests. When the normal
        # Easynews VIDEO search produces few results, resolve only TMDB's
        # canonical original_title and retry Easynews V3 with that title.
        tmdb_title_fallback_enabled = (
            os.environ.get(
                "EASYNEWS_TMDB_ORIGINAL_TITLE_FALLBACK",
                "false",
            ).strip().lower()
            in {"1", "true", "yes", "on"}
        )

        try:
            tmdb_title_trigger = max(
                0,
                int(
                    os.environ.get(
                        "EASYNEWS_V3_NZB_TRIGGER",
                        "20",
                    )
                ),
            )
        except ValueError:
            tmdb_title_trigger = 20

        if (
            not fallback_query
            and t == "movie"
            and tmdb_title_fallback_enabled
            and len(items) < tmdb_title_trigger
        ):
            imdb_param = (
                request.args.get("imdbid")
                or request.args.get("imdb")
            )

            try:
                original_title = (
                    tmdb_original_title_hint
                    or _tmdb_original_title(imdb_param)
                )

                requested_title_key = re.sub(
                    r"\s+",
                    " ",
                    str(base_query or "").strip(),
                ).casefold()

                original_title_key = re.sub(
                    r"\s+",
                    " ",
                    str(original_title or "").strip(),
                ).casefold()

                if (
                    original_title
                    and original_title_key
                    and original_title_key != requested_title_key
                ):
                    alias_components = [original_title]

                    if (
                        year_int
                        and str(year_int) not in original_title
                    ):
                        alias_components.append(str(year_int))

                    alias_query = " ".join(alias_components).strip()
                    alias_tokens = _tokenize(alias_query)

                    alias_strict_phrase = (
                        _sanitize_phrase(alias_query)
                        if strict_requested
                        else None
                    )

                    APP.logger.info(
                        "TMDB original-title fallback: %r -> %r",
                        base_query,
                        original_title,
                    )

                    # V3-only retry. This deliberately does not invoke
                    # Easynews V2 fallback behavior.
                    alias_data = c._search_v3(
                        query=alias_query,
                        file_type="VIDEO",
                        per_page=250,
                        sort_field="relevance",
                        sort_dir="-",
                    )

                    alias_items = filter_and_map(
                        alias_data,
                        min_bytes=min_bytes,
                        query_tokens=alias_tokens,
                        query_meta=query_meta,
                        strict_phrase=alias_strict_phrase,
                        strict_match=strict_requested,
                    )

                    existing_keys = {
                        _release_merge_key(item)
                        for item in items
                        if _release_merge_key(item)
                    }

                    alias_added = 0

                    for alias_item in alias_items:
                        key = _release_merge_key(alias_item)

                        if key and key in existing_keys:
                            continue

                        items.append(alias_item)

                        if key:
                            existing_keys.add(key)

                        alias_added += 1

                    APP.logger.info(
                        "TMDB original-title fallback added %s "
                        "Easynews VIDEO release(s)",
                        alias_added,
                    )

                    # Even if VIDEO returned nothing, let the existing
                    # posted-NZB fallback search the resolved original title.
                    posted_base_query = original_title
                    posted_query_tokens = alias_tokens
                    posted_strict_phrase = alias_strict_phrase

            except Exception as e:
                # Metadata lookup must never break normal Easynews results.
                APP.logger.warning(
                    "TMDB original-title fallback failed for imdbid=%r: %s",
                    imdb_param,
                    e,
                )

        # Optional Easynews V3 posted-NZB fallback.
        #
        # Normal VIDEO search remains the primary path. Only when it
        # produces fewer than the configured number of useful results
        # do we perform one broad V3 ALL-files search and inspect posted
        # .nzb documents found there.
        nzb_fallback_enabled = (
            os.environ.get(
                "EASYNEWS_V3_NZB_FALLBACK",
                "false",
            ).strip().lower()
            in {"1", "true", "yes", "on"}
        )

        if (
            not fallback_query
            and nzb_fallback_enabled
        ):
            try:
                trigger = max(
                    0,
                    int(
                        os.environ.get(
                            "EASYNEWS_V3_NZB_TRIGGER",
                            "20",
                        )
                    ),
                )
            except ValueError:
                trigger = 20

            if len(items) < trigger:
                try:
                    max_candidates = max(
                        1,
                        min(
                            int(
                                os.environ.get(
                                    "EASYNEWS_V3_NZB_MAX",
                                    "8",
                                )
                            ),
                            20,
                        ),
                    )
                except ValueError:
                    max_candidates = 8

                try:
                    nzb_concurrency = max(
                        1,
                        min(
                            int(
                                os.environ.get(
                                    "EASYNEWS_V3_NZB_CONCURRENCY",
                                    "4",
                                )
                            ),
                            8,
                        ),
                    )
                except ValueError:
                    nzb_concurrency = 4

                # Movies benefit from dropping the year from the actual
                # broad Easynews query. Strict metadata filtering below
                # still enforces the requested year.
                #
                # TV searches keep SxxExx/Sxx in the query to avoid an
                # unnecessarily huge title-only result set.
                if t == "movie" and posted_base_query:
                    broad_query = posted_base_query
                else:
                    broad_query = q

                try:
                    APP.logger.info(
                        "Easynews posted-NZB fallback triggered for %r "
                        "(normal results=%s, trigger=%s)",
                        broad_query,
                        len(items),
                        trigger,
                    )

                    broad_data = c._search_v3(
                        query=broad_query,
                        file_type="ALL",
                        per_page=200,
                        sort_field="relevance",
                        sort_dir="-",
                    )

                    posted_items = map_posted_nzbs(
                        c,
                        broad_data,
                        min_bytes=min_bytes,
                        query_tokens=posted_query_tokens,
                        query_meta=query_meta,
                        strict_phrase=posted_strict_phrase,
                        strict_match=strict_requested,
                        max_candidates=max_candidates,
                        concurrency=nzb_concurrency,
                    )

                    # Avoid obvious duplicates between normal VIDEO
                    # results and posted-NZB releases.
                    existing_keys = {
                        _release_merge_key(item)
                        for item in items
                        if _release_merge_key(item)
                    }

                    added = 0

                    for posted_item in posted_items:
                        key = _release_merge_key(posted_item)

                        if key and key in existing_keys:
                            continue

                        items.append(posted_item)

                        if key:
                            existing_keys.add(key)

                        added += 1

                    APP.logger.info(
                        "Easynews posted-NZB fallback added %s "
                        "validated release(s)",
                        added,
                    )

                except Exception as e:
                    # The fallback must never break normal Easynews
                    # VIDEO search results.
                    APP.logger.warning(
                        "Easynews posted-NZB fallback failed for %r: %s",
                        broad_query,
                        e,
                    )

        # Trim by limit (handles fallback and real queries)
        items = items[offset : offset + limit]

        display_q = raw_query if raw_query else q
        chan_title = f"Results for {display_q}"
        now_dt = datetime.now(timezone.utc)
        channel_pub = now_dt.strftime("%a, %d %b %Y %H:%M:%S %z")

        header = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<rss version="2.0" xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/">'
            "<channel>"
            f"<title>{xml_escape(chan_title)}</title>"
            f"<description>{xml_escape(chan_title)}</description>"
            f"<link>{request.url_root.rstrip('/')}/api</link>"
            f"<pubDate>{channel_pub}</pubDate>"
        )

        body_parts: List[str] = []
        for it in items:
            enc_id = encode_id(it)
            title = xml_escape(it["title"]) if it["title"] else "Untitled"
            link = f"{request.url_root.rstrip('/')}/api?t=get&id={enc_id}&apikey={request.args.get('apikey')}"
            safe_link = xml_escape(link)
            size = it["size"]
            guid = enc_id
            poster = it.get("poster")
            posted_dt = _coerce_datetime(it.get("posted")) or now_dt
            posted_str = posted_dt.strftime("%a, %d %b %Y %H:%M:%S %z")
            posted_epoch = str(int(posted_dt.timestamp()))
            duration_hms = it.get("duration_hms")
            quality = it.get("quality")
            thumb = it.get("thumbnail")
            year = it.get("year")
            season = it.get("season")
            episode = it.get("episode")

            # Easynews V3 rich media metadata
            resolution_raw = it.get("resolution_raw")
            video_codec = it.get("video_codec")
            audio_codec = it.get("audio_codec")
            audio_languages = it.get("audio_languages")
            subtitle_languages = it.get("subtitle_languages")

            def normalize_metadata_values(value):
                if value is None:
                    return None

                if isinstance(value, str):
                    values = [value]
                elif isinstance(value, (list, tuple, set)):
                    values = list(value)
                else:
                    values = [value]

                cleaned = []
                seen = set()

                for entry in values:
                    if isinstance(entry, dict):
                        entry = (
                            entry.get("language")
                            or entry.get("lang")
                            or entry.get("code")
                            or entry.get("name")
                        )

                    if entry is None:
                        continue

                    text = str(entry).strip()
                    if not text:
                        continue

                    key = text.lower()
                    if key in seen:
                        continue

                    seen.add(key)
                    cleaned.append(text)

                return ",".join(cleaned) if cleaned else None

            audio_language_value = normalize_metadata_values(audio_languages)
            subtitle_language_value = normalize_metadata_values(subtitle_languages)

            title_text = it.get("title", "")
            title_metadata = {
                "season": season,
                "episode": episode,
                "year": year,
                "quality": quality,
            }
            category_id = _detect_category(title_text, title_metadata)

            attr_parts = [
                f'<newznab:attr name="size" value="{size}"/>',
                f'<newznab:attr name="category" value="{category_id}"/>',
                f'<newznab:attr name="usenetdate" value="{posted_str}"/>',
                f'<newznab:attr name="posted" value="{posted_epoch}"/>',
            ]
            if poster:
                attr_parts.append(
                    f'<newznab:attr name="poster" value="{xml_escape(poster)}"/>'
                )
            if quality:
                attr_parts.append(
                    f'<newznab:attr name="quality" value="{xml_escape(quality)}"/>'
                )
            if duration_hms:
                attr_parts.append(
                    f'<newznab:attr name="duration" value="{duration_hms}"/>'
                )
            if thumb:
                attr_parts.append(
                    f'<newznab:attr name="thumb" value="{xml_escape(thumb)}"/>'
                )
            if year:
                attr_parts.append(f'<newznab:attr name="year" value="{year}"/>')
            if season:
                attr_parts.append(f'<newznab:attr name="season" value="{season}"/>')
            if episode:
                attr_parts.append(f'<newznab:attr name="episode" value="{episode}"/>')

            # Easynews V3 rich metadata
            if audio_language_value:
                attr_parts.append(
                    f'<newznab:attr name="language" value="{xml_escape(audio_language_value)}"/>'
                )

            if subtitle_language_value:
                attr_parts.append(
                    f'<newznab:attr name="subs" value="{xml_escape(subtitle_language_value)}"/>'
                )

            if resolution_raw:
                attr_parts.append(
                    f'<newznab:attr name="resolution" value="{xml_escape(str(resolution_raw))}"/>'
                )

            if video_codec:
                attr_parts.append(
                    f'<newznab:attr name="video" value="{xml_escape(str(video_codec))}"/>'
                )

            if audio_codec:
                attr_parts.append(
                    f'<newznab:attr name="audio" value="{xml_escape(str(audio_codec))}"/>'
                )

            attr_xml = "".join(attr_parts)
            item_xml = (
                f"<item>"
                f"<title>{title}</title>"
                f'<guid isPermaLink="false">{guid}</guid>'
                f"<link>{safe_link}</link>"
                f"<category>{category_id}</category>"
                f"<pubDate>{posted_str}</pubDate>"
                f"{attr_xml}"
                f'<enclosure url="{safe_link}" length="{size}" type="application/x-nzb"/>'
                f"</item>"
            )
            body_parts.append(item_xml)

        footer = "</channel></rss>"
        xml = header + "".join(body_parts) + footer
        return Response(xml, mimetype="application/rss+xml")

    if t in ("get", "getnzb"):
        enc_id = request.args.get("id")
        if not enc_id:
            return Response("Missing id", status=400)
        d = decode_id(enc_id)
        if d.get("sample"):
            title = d.get("title", "Sample Item")
            safe_title = "sample"
            nzb_content = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">'
                '<file subject="Sample Matrix Clip" date="0" poster="sample@example.com">'
                "<groups><group>alt.binaries.sample</group></groups>"
                '<segments><segment bytes="1024" number="1">sample</segment></segments>'
                "</file></nzb>"
            ).encode("utf-8")
            resp = Response(nzb_content, mimetype="application/x-nzb")
            resp.headers["Content-Disposition"] = (
                f'attachment; filename="{safe_title}.nzb"'
            )
            return resp
        if d.get("source") == "posted_nzb":
            mid = d.get("mid")

            if not mid:
                return Response(
                    "Missing posted NZB Message-ID",
                    status=400,
                )

            try:
                c = client()
                nzb_content = c.download_posted_nzb(mid)

            except EasynewsError as e:
                return Response(
                    f"Upstream error: {e}",
                    status=502,
                )

            title = (
                d.get("title")
                or d.get("filename")
                or "download"
            )

            safe_title = (
                "".join(
                    ch
                    for ch in title
                    if ch.isalnum()
                    or ch in (" ", "-", "_", ".")
                )[:200].strip()
                or "download"
            )

            if safe_title.lower().endswith(".nzb"):
                safe_title = safe_title[:-4]

            resp = Response(
                nzb_content,
                mimetype="application/x-nzb",
            )

            resp.headers["Content-Disposition"] = (
                f'attachment; filename="{safe_title}.nzb"'
            )

            return resp

        si = to_search_item(d)
        try:
            c = client()
            payload = c.build_nzb_payload([si], name=d.get("title"))
            # fetch content
            url = "https://members.easynews.com/2.0/api/dl-nzb"
            r = c.s.post(url, data=payload, timeout=60)
        except EasynewsError as e:
            return Response(f"Upstream error: {e}", status=502)
        except requests.exceptions.RequestException as e:
            return Response(f"Upstream network error: {e}", status=502)
        if r.status_code != 200:
            return Response(f"Upstream error {r.status_code}", status=502)
        # Name file as title.nzb
        title = d.get("title") or (d.get("filename", "download") + d.get("ext", ""))
        safe_title = (
            "".join(ch for ch in title if ch.isalnum() or ch in (" ", "-", "_", "."))[
                :200
            ].strip()
            or "download"
        )
        resp = Response(r.content, mimetype="application/x-nzb")
        resp.headers["Content-Disposition"] = f'attachment; filename="{safe_title}.nzb"'
        return resp

    return Response("Unsupported 't' parameter", status=400)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8081))
    APP.run(host="0.0.0.0", port=port)
