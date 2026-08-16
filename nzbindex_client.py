import re
import requests
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional


NZBINDEX_RSS_URL = "https://nzbindex.com/rss"
NZBINDEX_DOWNLOAD_URL = "https://nzbindex.com/download/{collection_id}.nzb"

_UUID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)


def _extract_collection_id(*values: Optional[str]) -> Optional[str]:
    for value in values:
        if not value:
            continue

        match = _UUID_RE.search(value)
        if match:
            return match.group(1).lower()

    return None


def search_nzbindex(
    query: str,
    max_results: int = 250,
    timeout: int = 10,
) -> List[Dict]:
    query = (query or "").strip()
    if not query:
        return []

    max_results = max(1, min(int(max_results), 250))

    response = requests.get(
        NZBINDEX_RSS_URL,
        params={
            "more": "1",
            "max": str(max_results),
            "hidecross": "0",
            "minsize": "1",
            "q": query,
        },
        timeout=timeout,
        headers={
            "User-Agent": "Easynews-as-indexer/1.0",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        },
    )
    response.raise_for_status()

    root = ET.fromstring(response.content)

    results = []

    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()

        collection_id = _extract_collection_id(link, guid)

        if not collection_id:
            continue

        enclosure = item.find("enclosure")

        size = 0
        if enclosure is not None:
            try:
                size = int(enclosure.get("length") or 0)
            except (TypeError, ValueError):
                size = 0

        results.append(
            {
                "source": "nzbindex",
                "id": collection_id,
                "title": title,
                "size": size,
                "pub_date": pub_date,
                "link": link,
                "guid": guid,
            }
        )

    return results


def download_nzbindex_nzb(
    collection_id: str,
    timeout: int = 20,
) -> bytes:
    collection_id = _extract_collection_id(collection_id)

    if not collection_id:
        raise ValueError("Invalid NZBIndex collection ID")

    response = requests.get(
        NZBINDEX_DOWNLOAD_URL.format(collection_id=collection_id),
        timeout=timeout,
        headers={
            "User-Agent": "Easynews-as-indexer/1.0",
            "Accept": "application/x-nzb, application/xml, text/xml, */*",
        },
    )
    response.raise_for_status()

    payload = response.content

    # Validate that NZBIndex really returned an NZB/XML document.
    root = ET.fromstring(payload)

    if not root.tag.lower().endswith("nzb"):
        raise ValueError("NZBIndex response is not a valid NZB")

    return payload
