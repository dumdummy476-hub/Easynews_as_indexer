from __future__ import annotations

import queue
from contextlib import contextmanager
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .client_errors import EasynewsError
from .config import Settings
from .metrics import METRICS


class SessionPool:
    def __init__(self, username: str, password: str, settings: Settings):
        self.settings = settings
        self._pool: queue.LifoQueue[requests.Session] = queue.LifoQueue()
        for _ in range(max(2, settings.v3_concurrency + 2)):
            self._pool.put(self._new_session(username, password))

    @staticmethod
    def _new_session(username: str, password: str) -> requests.Session:
        session = requests.Session()
        session.auth = (username, password)
        session.headers.update({
            "User-Agent": "Mozilla/5.0 EasynewsIndexer/2.0",
            "Accept": "application/json, text/javascript, */*; q=0.9",
        })
        retry = Retry(
            total=2, connect=2, read=2, backoff_factor=0.35,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "POST"}), respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(pool_connections=16, pool_maxsize=32, max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    @contextmanager
    def session(self):
        session = self._pool.get()
        try:
            yield session
        finally:
            self._pool.put(session)

    def get_json(self, url: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
        METRICS.inc("upstream_requests_total")
        try:
            with self.session() as session:
                response = session.get(url, params=params, timeout=(self.settings.connect_timeout, timeout))
        except requests.RequestException as exc:
            METRICS.inc("upstream_errors_total")
            raise EasynewsError(f"Easynews request failed: {exc}") from exc
        if response.status_code in (401, 403):
            raise EasynewsError("Unauthorized; check Easynews credentials")
        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            METRICS.inc("upstream_errors_total")
            raise EasynewsError(f"Easynews HTTP error: {response.status_code}") from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise EasynewsError("Easynews returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise EasynewsError("Easynews returned unexpected JSON")
        return data

    def post_bytes(self, url: str, data: dict[str, str], timeout: float) -> bytes:
        try:
            with self.session() as session:
                response = session.post(url, data=data, timeout=(self.settings.connect_timeout, timeout))
        except requests.RequestException as exc:
            raise EasynewsError(f"Easynews POST failed: {exc}") from exc
        if response.status_code != 200:
            raise EasynewsError(f"Easynews POST failed: HTTP {response.status_code}")
        return response.content
