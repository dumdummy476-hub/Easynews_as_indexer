from __future__ import annotations

import base64
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from .client_errors import EasynewsError
from .config import Settings
from .metrics import METRICS
from .nntp import download_posted_nzb as _download_posted_nzb
from .nntp import inspect_posted_nzb as _inspect_posted_nzb
from .nntp import validate_nzb
from .transport import SessionPool

EASYNEWS_BASE = "https://members.easynews.com"
logger = logging.getLogger(__name__)


@dataclass
class SearchItem:
    id: str | None
    hash: str
    filename: str
    ext: str
    sig: str | None
    type: str
    raw: dict[str, Any]

    @property
    def value_token(self) -> str:
        fn_b64 = base64.b64encode(self.filename.encode()).decode().replace("=", "")
        ext_b64 = base64.b64encode(self.ext.encode()).decode().replace("=", "")
        return f"{self.hash}|{fn_b64}:{ext_b64}"


class EasynewsClient:
    def __init__(self, username: str, password: str, settings: Settings | None = None):
        self.username = username; self.password = password
        self.settings = settings or Settings.from_env()
        self.transport = SessionPool(username, password, self.settings)

    def login(self) -> None:
        mode = self.settings.search_api
        if mode == "v2":
            self._search_v2("test", page=1, per_page=1); return
        try:
            self._search_v3_page("test", 1, "VIDEO", "relevance", "-", 0)
        except EasynewsError:
            if mode != "auto":
                raise
            self._search_v2("test", page=1, per_page=1)

    def _search_v2(self, query: str, page: int = 1, per_page: int = 250,
                   request_timeout: float | None = None, **_: Any) -> dict[str, Any]:
        params = {
            "fly":"2", "sb":"1", "pno":str(page), "pby":str(min(max(1, per_page), 250)),
            "u":"1", "chxu":"1", "chxgx":"1", "st":"basic", "gps":query, "vv":"1", "safeO":"0",
            "fty[]":"VIDEO", "s1":"relevance", "s1d":"-",
        }
        return self.transport.get_json(f"{EASYNEWS_BASE}/2.0/search/solr-search/", params,
                                       request_timeout or self.settings.search_timeout)

    def _search_v3_page(self, query: str, page: int = 1, file_type: str = "VIDEO",
                        sort_field: str | None = "relevance", sort_dir: str = "-", safe_off: int = 0,
                        request_timeout: float | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"gps":query, "pno":str(page), "u":"1", "safeO":str(safe_off),
                                  "s1":sort_field or "relevance", "s1d":sort_dir}
        if (file_type or "VIDEO").upper() not in {"ALL", "ANY", "*"}:
            params["fty[]"] = "VIDEO"
        return self.transport.get_json(f"{EASYNEWS_BASE}/3.0/api/search", params,
                                       request_timeout or self.settings.search_timeout)

    def _search_v3(self, query: str, file_type: str = "VIDEO", page: int = 1, per_page: int = 250,
                   sort_field: str | None = "relevance", sort_dir: str = "-", safe_off: int = 0,
                   deadline: float | None = None) -> dict[str, Any]:
        first_page_num = max(1, int(page)); first_timeout = self.settings.search_timeout
        if deadline is not None:
            if deadline <= time.monotonic():
                raise EasynewsError("Search deadline exceeded")
            first_timeout = max(0.5, min(first_timeout, deadline - time.monotonic()))
        first = self._search_v3_page(query, first_page_num, file_type, sort_field, sort_dir, safe_off, first_timeout)
        try: total_pages = max(1, int(first.get("numPages") or 1))
        except (TypeError, ValueError): total_pages = 1
        requested_pages = max(1, (max(1, int(per_page)) + 99) // 100)
        pages_to_fetch = min(requested_pages, self.settings.v3_max_pages, max(1, total_pages - first_page_num + 1))
        remaining = list(range(first_page_num + 1, first_page_num + pages_to_fetch)); page_results = {}
        def fetch_page(pno: int):
            if deadline is not None and time.monotonic() >= deadline:
                raise EasynewsError("Search deadline exceeded")
            timeout = self.settings.search_timeout
            if deadline is not None: timeout = max(0.5, min(timeout, deadline - time.monotonic()))
            return self._search_v3_page(query, pno, file_type, sort_field, sort_dir, safe_off, timeout)
        if remaining:
            with ThreadPoolExecutor(max_workers=min(self.settings.v3_concurrency, len(remaining))) as pool:
                futures = {pool.submit(fetch_page, pno): pno for pno in remaining}
                for future in as_completed(futures):
                    pno = futures[future]
                    try:
                        response = future.result(); data = response.get("data")
                        if int(response.get("page") or pno) != pno or not isinstance(data, list):
                            raise EasynewsError(f"Invalid V3 page {pno}")
                        page_results[pno] = response
                    except Exception as exc:
                        logger.warning("Easynews V3 page %s failed: %s", pno, exc); METRICS.inc("v3_page_failures_total")
        merged = []; seen = set()
        def add(response: dict[str, Any]):
            for item in response.get("data") or []:
                key = (item.get("hash") or item.get("0") or item.get("id")) if isinstance(item, dict) else item[0] if isinstance(item, list) and item else None
                if key is not None:
                    text = str(key)
                    if text in seen: continue
                    seen.add(text)
                merged.append(item)
        add(first)
        for pno in sorted(page_results): add(page_results[pno])
        result = dict(first); result["data"] = merged; result["returned"] = len(merged); result["page"] = first_page_num
        return result

    def search(self, query: str, file_type: str = "VIDEO", page: int = 1, per_page: int = 250,
               sort_field: str | None = "relevance", sort_dir: str = "-", safe_off: int = 0,
               deadline: float | None = None) -> dict[str, Any]:
        mode = self.settings.search_api
        if mode == "v2":
            timeout = self._remaining_timeout(deadline)
            return self._search_v2(query, page=page, per_page=per_page, request_timeout=timeout)
        try:
            return self._search_v3(query, file_type, page, per_page, sort_field, sort_dir, safe_off, deadline)
        except EasynewsError:
            if mode != "auto": raise
            METRICS.inc("v3_to_v2_fallback_total")
            return self._search_v2(query, page=page, per_page=per_page, request_timeout=self._remaining_timeout(deadline))

    def _remaining_timeout(self, deadline: float | None) -> float:
        timeout = self.settings.search_timeout
        if deadline is not None:
            if deadline <= time.monotonic(): raise EasynewsError("Search deadline exceeded")
            timeout = max(0.5, min(timeout, deadline - time.monotonic()))
        return timeout

    @staticmethod
    def _collect_items(json_data: dict[str, Any]) -> list[SearchItem]:
        items = []
        for raw in json_data.get("data") or []:
            if isinstance(raw, list) and len(raw) >= 12:
                hash_id, filename, ext, sig, item_id, typ, obj = raw[0], raw[10], raw[11], None, None, "VIDEO", {}
            elif isinstance(raw, dict):
                hash_id = raw.get("hash") or raw.get("0") or raw.get("id"); filename = raw.get("fn") or raw.get("filename") or raw.get("10") or ""
                ext = raw.get("extension") or raw.get("ext") or raw.get("11") or ""; sig = raw.get("sig"); item_id = raw.get("id"); typ = str(raw.get("type") or "VIDEO"); obj = raw
            else: continue
            if hash_id and ext:
                items.append(SearchItem(str(item_id) if item_id else None, str(hash_id), str(filename), str(ext), str(sig) if sig else None, typ, obj))
        return items

    def build_nzb_payload(self, items: list[SearchItem], name: str | None = None) -> dict[str, str]:
        data = {"autoNZB":"1"}
        for idx, item in enumerate(items): data[str(idx) if not item.sig else f"{idx}&sig={item.sig}"] = item.value_token
        if name: data["nameZipQ0"] = name
        return data

    @staticmethod
    def validate_nzb_bytes(content: bytes) -> bytes: return validate_nzb(content)

    def download_nzb_bytes(self, payload: dict[str, str]) -> bytes:
        METRICS.inc("nzb_download_requests_total")
        return validate_nzb(self.transport.post_bytes(f"{EASYNEWS_BASE}/2.0/api/dl-nzb", payload, self.settings.download_timeout))

    def download_nzb(self, payload: dict[str, str], out_path: str) -> str:
        content = self.download_nzb_bytes(payload); os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "wb") as handle: handle.write(content)
        return out_path

    @staticmethod
    def _decode_yenc(encoded: bytes) -> bytes:
        from .nntp import decode_yenc
        return decode_yenc(encoded)

    def download_posted_nzb(self, message_id: str, sibling_message_ids: list[str] | None = None) -> bytes:
        return _download_posted_nzb(self.settings, self.username, self.password, message_id, sibling_message_ids)

    def inspect_posted_nzb(self, message_id: str, sibling_message_ids: list[str] | None = None) -> dict[str, Any]:
        return _inspect_posted_nzb(self.settings, self.username, self.password, message_id, sibling_message_ids)


__all__ = ["EasynewsClient", "EasynewsError", "SearchItem"]
