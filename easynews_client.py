"""
Unofficial Easynews client for Newznab-compatible search and NZB generation.

Search support:
- Easynews Search API V3 via /3.0/api/search (default)
- Legacy Easynews Search API V2 via /2.0/search/solr-search
- Optional automatic V3 -> V2 fallback
- Parallel pagination for V3 searches

NZB generation continues to use /2.0/api/dl-nzb.

Authentication uses HTTP Basic Auth with a valid Easynews account.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests
from requests.exceptions import RequestException


EASYNEWS_BASE = "https://members.easynews.com"

_LOGIN_TIMEOUT = 15
_SEARCH_TIMEOUT = 30
_DOWNLOAD_TIMEOUT = 60

logger = logging.getLogger(__name__)


class EasynewsError(Exception):
    pass


@dataclass
class SearchItem:
    id: Optional[str]
    hash: str
    filename: str
    ext: str
    sig: Optional[str]
    type: str
    raw: Dict[str, Any]

    @property
    def value_token(self) -> str:
        """
        Build the value string Easynews expects for checkbox selections:
        format: "{hash}|{b64(filename)}:{b64(ext)}"
        As seen in members.js createNZB -> it reads from input[checkbox].value
        """
        fn_b64 = base64.b64encode(self.filename.encode()).decode().replace("=", "")
        ext_b64 = base64.b64encode(self.ext.encode()).decode().replace("=", "")
        return f"{self.hash}|{fn_b64}:{ext_b64}"


class EasynewsClient:
    def __init__(
        self, username: str, password: str, session: Optional[requests.Session] = None
    ):
        self.username = username
        self.password = password
        self.s = session or requests.Session()
        # Default headers
        self.s.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EasynewsClient/1.0",
                "Accept": "application/json, text/javascript, */*; q=0.9",
            }
        )
        # Use HTTP Basic Auth for endpoints that support it
        self.s.auth = (self.username, self.password)

    def login(self) -> None:
        """
        Validate Easynews credentials using the configured search API.

        EASYNEWS_SEARCH_API:
          v3   - validate against Easynews Search API 3.0
          v2   - validate against legacy Search API 2.0
          auto - try V3 first, then V2 if V3 is unavailable
        """
        mode = os.environ.get("EASYNEWS_SEARCH_API", "v3").strip().lower()

        if mode not in {"v2", "v3", "auto"}:
            logger.warning(
                "Unknown EASYNEWS_SEARCH_API=%r during login; defaulting to v3",
                mode,
            )
            mode = "v3"

        try:
            if mode == "v2":
                self._search_v2(
                    query="test",
                    page=1,
                    per_page=1,
                    file_type="VIDEO",
                    sort_field="relevance",
                    sort_dir="-",
                    safe_off=0,
                )
                return

            try:
                self._search_v3_page(
                    query="test",
                    page=1,
                    file_type="VIDEO",
                    sort_field="relevance",
                    sort_dir="-",
                    safe_off=0,
                )
                return
            except EasynewsError:
                if mode != "auto":
                    raise

                logger.warning(
                    "Easynews V3 login validation failed; trying V2"
                )

            self._search_v2(
                query="test",
                page=1,
                per_page=1,
                file_type="VIDEO",
                sort_field="relevance",
                sort_dir="-",
                safe_off=0,
            )

        except EasynewsError:
            raise
        except Exception as e:
            logger.exception("Network error during Easynews login")
            raise EasynewsError(
                f"Network error during Easynews login: {e}"
            ) from e

    def _search_v2(
        self,
        query: str,
        file_type: str = "VIDEO",
        page: int = 1,
        per_page: int = 250,
        sort_field: Optional[str] = "relevance",
        sort_dir: str = "-",
        safe_off: int = 0,
    ) -> Dict[str, Any]:
        """
        Legacy Easynews Search API 2.0 fallback.

        V2 supports up to 250 results per request. Keep this implementation
        available for EASYNEWS_SEARCH_API=v2 or as an automatic fallback.
        """
        if file_type != "VIDEO":
            file_type = "VIDEO"

        params = {
            "fly": "2",
            "sb": "1",
            "pno": str(page),
            "pby": str(min(max(1, int(per_page)), 250)),
            "u": "1",
            "chxu": "1",
            "chxgx": "1",
            "st": "basic",
            "gps": query,
            "vv": "1",
            "safeO": str(safe_off),
        }

        if sort_field:
            params["s1"] = sort_field
            params["s1d"] = sort_dir

        url = f"{EASYNEWS_BASE}/2.0/search/solr-search/"

        try:
            r = self.s.get(
                url,
                params={**params, "fty[]": file_type},
                timeout=_SEARCH_TIMEOUT,
            )

            if r.status_code in (401, 403):
                raise EasynewsError("Unauthorized; check username/password")

            r.raise_for_status()

            try:
                return r.json()
            except ValueError as e:
                raise EasynewsError(
                    "Easynews V2 returned invalid JSON"
                ) from e

        except EasynewsError:
            raise
        except RequestException as e:
            logger.exception(
                "Easynews V2 search failed for query '%s'",
                query,
            )
            raise EasynewsError(
                f"Easynews V2 search request failed: {e}"
            ) from e

    def _search_v3_page(
        self,
        query: str,
        page: int = 1,
        file_type: str = "VIDEO",
        sort_field: Optional[str] = "relevance",
        sort_dir: str = "-",
        safe_off: int = 0,
    ) -> Dict[str, Any]:
        """
        Fetch one page from Easynews Search API 3.0.

        V3 always returns up to 100 results per page and ignores page-size
        parameters such as pby/dni.
        """
        if file_type != "VIDEO":
            file_type = "VIDEO"

        params = {
            "gps": query,
            "pno": str(page),
            "u": "1",
            "safeO": str(safe_off),
            "s1": sort_field or "relevance",
            "s1d": sort_dir,
            "fty[]": file_type,
        }

        url = f"{EASYNEWS_BASE}/3.0/api/search"

        for attempt in range(1, 4):
            try:
                # Use an independent request rather than sharing Session state
                # between worker threads. Authentication is HTTP Basic Auth.
                r = requests.get(
                    url,
                    params=params,
                    auth=(self.username, self.password),
                    headers=dict(self.s.headers),
                    timeout=_SEARCH_TIMEOUT,
                )

                if r.status_code in (401, 403):
                    raise EasynewsError("Unauthorized; check username/password")

                r.raise_for_status()

                if not r.content:
                    raise EasynewsError(
                        f"Easynews V3 returned an empty response for page {page}"
                    )

                try:
                    return r.json()
                except ValueError as e:
                    raise EasynewsError(
                        f"Easynews V3 returned invalid JSON for page {page}"
                    ) from e

            except EasynewsError:
                raise

            except RequestException as e:
                if attempt < 3:
                    delay = 0.5 * attempt
                    logger.warning(
                        "Easynews V3 transient request failure for query %r, page %s "
                        "(attempt %s/3): %s; retrying in %.1fs",
                        query,
                        page,
                        attempt,
                        e,
                        delay,
                    )
                    time.sleep(delay)
                    continue

                logger.exception(
                    "Easynews V3 search failed for query %r, page %s after 3 attempts",
                    query,
                    page,
                )
                raise EasynewsError(
                    f"Easynews V3 search request failed on page {page} after 3 attempts: {e}"
                ) from e

    def _search_v3(
        self,
        query: str,
        file_type: str = "VIDEO",
        page: int = 1,
        per_page: int = 250,
        sort_field: Optional[str] = "relevance",
        sort_dir: str = "-",
        safe_off: int = 0,
    ) -> Dict[str, Any]:
        """
        Search Easynews using API 3.0.

        V3 has a fixed page size of 100. The legacy bridge requested 250
        candidates, so a per_page=250 request maps to three V3 pages.

        Page 1 is fetched first to learn numPages. Remaining pages are fetched
        concurrently and merged. Duplicate hashes are removed.
        """
        if file_type != "VIDEO":
            file_type = "VIDEO"

        first_page_num = max(1, int(page))

        first = self._search_v3_page(
            query=query,
            page=first_page_num,
            file_type=file_type,
            sort_field=sort_field,
            sort_dir=sort_dir,
            safe_off=safe_off,
        )

        try:
            total_pages = max(1, int(first.get("numPages") or 1))
        except (TypeError, ValueError):
            total_pages = 1

        # V3 always returns 100/page.
        # 250 requested candidates => ceil(250 / 100) => 3 pages.
        requested_pages = max(1, (max(1, int(per_page)) + 99) // 100)

        # Safety/tuning knob. Default 3 keeps behavior close to the old
        # bridge's 250-result V2 search while allowing overrides later.
        try:
            configured_max_pages = max(
                1, int(os.environ.get("EASYNEWS_V3_MAX_PAGES", "3"))
            )
        except ValueError:
            configured_max_pages = 3

        pages_to_fetch = min(
            requested_pages,
            configured_max_pages,
            max(1, total_pages - first_page_num + 1),
        )

        remaining_pages = list(
            range(
                first_page_num + 1,
                first_page_num + pages_to_fetch,
            )
        )

        page_results: Dict[int, Dict[str, Any]] = {}

        if remaining_pages:
            try:
                configured_concurrency = max(
                    1,
                    min(
                        10,
                        int(os.environ.get("EASYNEWS_V3_CONCURRENCY", "10")),
                    ),
                )
            except ValueError:
                configured_concurrency = 10

            max_workers = min(
                configured_concurrency,
                len(remaining_pages),
            )

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        self._search_v3_page,
                        query,
                        pno,
                        file_type,
                        sort_field,
                        sort_dir,
                        safe_off,
                    ): pno
                    for pno in remaining_pages
                }

                for future in as_completed(futures):
                    pno = futures[future]
                    try:
                        page_results[pno] = future.result()
                    except Exception as e:
                        # Keep successful pages instead of failing the entire
                        # Prowlarr search because one secondary page failed.
                        logger.warning(
                            "Easynews V3 page %s failed for query '%s': %s",
                            pno,
                            query,
                            e,
                        )

        merged_data: List[Any] = []
        seen_hashes = set()

        def add_items(response: Dict[str, Any]) -> None:
            for item in response.get("data", []):
                hash_id = None

                if isinstance(item, dict):
                    hash_id = (
                        item.get("hash")
                        or item.get("0")
                        or item.get("id")
                    )
                elif isinstance(item, list) and item:
                    hash_id = item[0]

                # Deduplicate normal Easynews results by hash.
                if hash_id:
                    key = str(hash_id)
                    if key in seen_hashes:
                        continue
                    seen_hashes.add(key)

                merged_data.append(item)

        add_items(first)

        # Preserve page order despite concurrent requests.
        for pno in sorted(page_results):
            add_items(page_results[pno])

        merged = dict(first)
        merged["data"] = merged_data
        merged["returned"] = len(merged_data)
        merged["page"] = first_page_num

        logger.info(
            "Easynews V3 search '%s': %s candidates from %s page(s)",
            query,
            len(merged_data),
            1 + len(page_results),
        )

        return merged

    def search(
        self,
        query: str,
        file_type: str = "VIDEO",
        page: int = 1,
        per_page: int = 250,
        sort_field: Optional[str] = "relevance",
        sort_dir: str = "-",
        safe_off: int = 0,
    ) -> Dict[str, Any]:
        """
        Public search dispatcher.

        EASYNEWS_SEARCH_API:
          v3   - use Easynews Search API 3.0 (default)
          v2   - use legacy Easynews Search API 2.0
          auto - try V3 first, then fall back to V2 on failure
        """
        mode = os.environ.get("EASYNEWS_SEARCH_API", "v3").strip().lower()

        if mode not in {"v2", "v3", "auto"}:
            logger.warning(
                "Unknown EASYNEWS_SEARCH_API=%r; defaulting to v3",
                mode,
            )
            mode = "v3"

        if mode == "v2":
            return self._search_v2(
                query=query,
                file_type=file_type,
                page=page,
                per_page=per_page,
                sort_field=sort_field,
                sort_dir=sort_dir,
                safe_off=safe_off,
            )

        try:
            return self._search_v3(
                query=query,
                file_type=file_type,
                page=page,
                per_page=per_page,
                sort_field=sort_field,
                sort_dir=sort_dir,
                safe_off=safe_off,
            )
        except EasynewsError:
            if mode != "auto":
                raise

            logger.warning(
                "Easynews V3 failed for query '%s'; falling back to V2",
                query,
            )

            return self._search_v2(
                query=query,
                file_type=file_type,
                page=page,
                per_page=per_page,
                sort_field=sort_field,
                sort_dir=sort_dir,
                safe_off=safe_off,
            )

    @staticmethod
    def _collect_items(json_data: Dict[str, Any]) -> List[SearchItem]:
        items: List[SearchItem] = []
        for it in json_data.get("data", []):
            hash_id = ""
            filename_no_ext = ""
            ext = ""
            sig: Optional[str] = None
            typ = ""
            item_id: Optional[str] = None

            if isinstance(it, list):
                if len(it) >= 12:
                    hash_id = it[0]
                    filename_no_ext = it[10]
                    ext = it[11]
            elif isinstance(it, dict):
                # Support both Easynews V3 named fields and legacy V2
                # numeric-string fields.
                hash_id = (
                    it.get("hash")
                    or it.get("0")
                    or it.get("id")
                    or ""
                )
                filename_no_ext = (
                    it.get("fn")
                    or it.get("filename")
                    or it.get("10")
                    or ""
                )
                ext = (
                    it.get("extension")
                    or it.get("ext")
                    or it.get("11")
                    or ""
                )
                sig = it.get("sig")
                typ = it.get("type") or "VIDEO"
                item_id = it.get("id")

            if not hash_id or not ext:
                # Skip malformed entries
                continue

            items.append(
                SearchItem(
                    id=item_id,
                    hash=hash_id,
                    filename=filename_no_ext,
                    ext=ext,
                    sig=sig,
                    type=typ,
                    raw=it if isinstance(it, dict) else {},
                )
            )
        return items

    def build_nzb_payload(
        self,
        items: List[SearchItem],
        name: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        Build the form-encoded payload expected by /2.0/api/dl-nzb.
        Emulates createNZB() from members.js which submits hidden inputs of the checked items.
        Keys look like "{index}&sig={sig}" and value is value_token.
        We'll just use sequential indexes starting at 0.
        """
        data: Dict[str, str] = {"autoNZB": "1"}
        for idx, it in enumerate(items):
            key = str(idx)
            if it.sig:
                key = f"{idx}&sig={it.sig}"
            data[key] = it.value_token
        if name:
            data["nameZipQ0"] = name
        # The site posts to /2.0/api/dl-nzb and returns the NZB file content (application/x-nzb or xml)
        return data

    def download_nzb(self, payload: Dict[str, str], out_path: str) -> str:
        url = f"{EASYNEWS_BASE}/2.0/api/dl-nzb"
        try:
            r = self.s.post(url, data=payload, stream=True, timeout=_DOWNLOAD_TIMEOUT)
        except RequestException as e:
            logger.exception("NZB download request failed")
            raise EasynewsError(f"NZB download request failed: {e}") from e
        if r.status_code != 200:
            raise EasynewsError(f"NZB creation failed: HTTP {r.status_code}")

        content_type = r.headers.get("Content-Type", "")
        if "xml" not in content_type and "nzb" not in content_type:
            # Sometimes returns text/html with a redirect page; still try to save
            pass

        content = r.content.replace(
            b'date=""', b'date="0"'
        )  # normalize empty NZB date fields
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(content)
        return out_path

    def search_and_nzb(
        self,
        query: str,
        file_type: str = "VIDEO",
        max_items: int = 5,
        nzb_name: Optional[str] = None,
        out_path: str = "download.nzb",
    ) -> str:
        data = self.search(query=query, file_type=file_type)
        items = self._collect_items(data)
        if not items:
            raise EasynewsError("No results found for query")
        sel = items[:max_items]
        payload = self.build_nzb_payload(sel, name=nzb_name)
        return self.download_nzb(payload, out_path)


__all__ = ["EasynewsClient", "EasynewsError", "SearchItem"]
