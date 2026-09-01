from __future__ import annotations

import html
import os
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import Any
from urllib.parse import urlencode

from flask import Flask, Response, jsonify, request

from .categories import caps_xml
from .client import EasynewsError, SearchItem
from .config import Settings
from .metrics import METRICS
from .search import SearchService
from .security import decode_signed, sign_payload


def _xml(s: Any) -> str:
    return html.escape(str(s), quote=True)


def _int(value: str | None, default: int | None = None) -> int | None:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _posted(value: Any) -> datetime:
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        try:
            raw = float(value)
            if raw > 10_000_000_000:
                raw /= 1000.0
            return datetime.fromtimestamp(raw, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            pass
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                dt = datetime.strptime(value.replace("Z", "+0000"), fmt)
                return dt.replace(tzinfo=dt.tzinfo or timezone.utc).astimezone(timezone.utc)
            except ValueError:
                continue
    return datetime.now(timezone.utc)


def _safe_filename(title: str) -> str:
    value = "".join(ch for ch in title if ch.isalnum() or ch in (" ", "-", "_", "."))[:200].strip()
    return value or "download"


def create_app(settings: Settings | None = None, service: SearchService | None = None) -> Flask:
    settings = settings or Settings.from_env()
    app = Flask(__name__)
    svc = service or SearchService(settings)
    app.extensions["search_service"] = svc
    app.extensions["settings"] = settings

    def authorized() -> bool:
        supplied = request.args.get("apikey") or request.headers.get("X-Api-Key")
        return bool(settings.api_key and supplied == settings.api_key)

    @app.get("/healthz")
    def healthz():
        return jsonify(status="ok", version="2.0.0")

    @app.get("/readyz")
    def readyz():
        if not settings.easynews_user or not settings.easynews_pass or not settings.api_key or settings.api_key == "testkey":
            return jsonify(status="not_ready", reason="configuration"), 503
        return jsonify(status="ready" if svc.ready else "configured", upstream_validated=svc.ready)

    @app.get("/metrics")
    def metrics():
        if settings.metrics_require_apikey and not authorized():
            return Response("Unauthorized", status=401)
        return Response(METRICS.render(), mimetype="text/plain")

    @app.get("/api")
    def api():
        if not authorized():
            return Response("Unauthorized", status=401)
        t = (request.args.get("t") or "caps").lower()
        if t == "caps":
            xml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<caps><server version="2.0.0" title="Easynews Indexer"/>'
                f'<limits max="{settings.max_limit}" default="{settings.default_limit}"/>'
                '<registration available="no" open="no"/>'
                '<searching>'
                '<search available="yes" supportedParams="q,cat,limit,offset,maxage,minsize,maxsize,sort"/>'
                '<movie-search available="yes" supportedParams="q,year,imdbid,cat,limit,offset,maxage,minsize,maxsize,sort"/>'
                '<tv-search available="yes" supportedParams="q,season,ep,tvdbid,cat,limit,offset,maxage,minsize,maxsize,sort"/>'
                '</searching>' + caps_xml() + '</caps>'
            )
            return Response(xml, mimetype="application/xml")
        if t in {"search", "movie", "tvsearch"}:
            q = (request.args.get("q") or "").strip()
            kind = "movie" if t == "movie" else "tv" if t == "tvsearch" else "search"
            year = _int(request.args.get("year") or request.args.get("yr"))
            season = _int(request.args.get("season") or request.args.get("seasonnum"))
            episode = _int(request.args.get("ep") or request.args.get("episode") or request.args.get("epnum"))
            imdbid = request.args.get("imdbid") or request.args.get("imdb")
            tvdbid = request.args.get("tvdbid") or request.args.get("tvdb")
            cats = {int(x) for x in (request.args.get("cat") or "").split(",") if x.strip().isdigit()}
            minsize = _int(request.args.get("minsize"), settings.default_min_size_mb)
            maxage = _int(request.args.get("maxage"))
            maxsize = _int(request.args.get("maxsize"))
            sort_mode = (request.args.get("sort") or settings.sort_mode).lower()
            strict_raw = request.args.get("strict")
            strict = (kind in {"movie", "tv"}) if strict_raw is None else strict_raw.lower() not in {"0", "false", "no", "off"}
            limit = max(1, min(settings.max_limit, _int(request.args.get("limit"), settings.default_limit) or settings.default_limit))
            offset = max(0, _int(request.args.get("offset"), 0) or 0)
            try:
                items = svc.search(kind=kind, query=q, year=year, season=season, episode=episode, imdbid=imdbid, tvdbid=tvdbid,
                                   categories=cats, min_size_mb=minsize, maxage=maxage, strict=strict, sort_mode=sort_mode)
                if maxsize is not None and maxsize >= 0:
                    max_bytes = maxsize * 1024 * 1024
                    items = [item for item in items if int(item.get("size") or 0) <= max_bytes]
            except EasynewsError as exc:
                return Response(f"Upstream error: {exc}", status=502)
            total = len(items)
            page = items[offset:offset+limit]
            base = request.url_root.rstrip("/") + "/api"
            channel = [
                '<?xml version="1.0" encoding="UTF-8"?>',
                '<rss version="2.0" xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/"><channel>',
                f'<title>{_xml("Easynews results for " + (q or imdbid or tvdbid or "search"))}</title>',
                f'<link>{_xml(base)}</link>',
                f'<newznab:response offset="{offset}" total="{total}"/>',
            ]
            for item in page:
                payload = {k: item.get(k) for k in ("hash", "filename", "ext", "sig", "title", "source", "mid", "mids") if item.get(k) is not None}
                token = sign_payload(payload, settings.signing_secret)
                params = urlencode({"t": "get", "id": token, "apikey": settings.api_key})
                link = f"{base}?{params}"
                dt = _posted(item.get("posted")); date_text = format_datetime(dt)
                category_values = item.get("categories") if isinstance(item.get("categories"), list) else [int(item.get("category") or 8000)]
                attrs = [f'<newznab:attr name="size" value="{int(item.get("size") or 0)}"/>']
                attrs.extend(f'<newznab:attr name="category" value="{int(category)}"/>' for category in category_values)
                attrs.extend([
                    f'<newznab:attr name="usenetdate" value="{_xml(date_text)}"/>',
                    f'<newznab:attr name="posted" value="{int(dt.timestamp())}"/>',
                ])
                for name, key in (("year", "year"), ("season", "season"), ("episode", "episode"), ("resolution", "quality"),
                                  ("video", "video_codec"), ("audio", "audio_codec"), ("group", "release_group")):
                    if item.get(key) is not None:
                        attrs.append(f'<newznab:attr name="{name}" value="{_xml(item[key])}"/>')
                if item.get("audio_languages"):
                    attrs.append(f'<newznab:attr name="language" value="{_xml(item["audio_languages"])}"/>')
                if item.get("subtitle_languages"):
                    attrs.append(f'<newznab:attr name="subs" value="{_xml(item["subtitle_languages"])}"/>')
                channel.append(
                    '<item>'
                    f'<title>{_xml(item.get("title") or "Untitled")}</title>'
                    f'<guid isPermaLink="false">{_xml(token)}</guid>'
                    f'<link>{_xml(link)}</link>'
                    f'<category>{int(item.get("category") or 8000)}</category>'
                    f'<pubDate>{_xml(date_text)}</pubDate>'
                    + ''.join(attrs) +
                    f'<enclosure url="{_xml(link)}" length="{int(item.get("size") or 0)}" type="application/x-nzb"/>'
                    '</item>'
                )
            channel.append('</channel></rss>')
            return Response(''.join(channel), mimetype="application/rss+xml")
        if t in {"get", "getnzb"}:
            token = request.args.get("id")
            if not token:
                return Response("Missing id", status=400)
            try:
                payload = decode_signed(token, settings.signing_secret, settings.allow_legacy_unsigned_ids)
            except Exception as exc:
                return Response(f"Invalid id: {exc}", status=400)
            try:
                if payload.get("source") == "posted_nzb":
                    content = svc.get_posted_nzb(str(payload.get("mid") or ""), payload.get("mids") if isinstance(payload.get("mids"), list) else None)
                else:
                    item = SearchItem(None, str(payload["hash"]), str(payload.get("filename") or ""), str(payload.get("ext") or ""), payload.get("sig"), "VIDEO", {})
                    content = svc.client.download_nzb_bytes(svc.client.build_nzb_payload([item], payload.get("title")))
            except (EasynewsError, KeyError) as exc:
                return Response(f"Upstream error: {exc}", status=502)
            title = _safe_filename(str(payload.get("title") or payload.get("filename") or "download"))
            if title.lower().endswith(".nzb"):
                title = title[:-4]
            response = Response(content, mimetype="application/x-nzb")
            response.headers["Content-Disposition"] = f'attachment; filename="{title}.nzb"'
            return response
        return Response("Unsupported 't' parameter", status=400)

    if os.getenv("EASYNEWS_VALIDATE_ON_STARTUP", "false").lower() in {"1", "true", "yes", "on"}:
        try:
            svc.validate()
        except Exception:
            app.logger.exception("Startup Easynews validation failed")
    return app
