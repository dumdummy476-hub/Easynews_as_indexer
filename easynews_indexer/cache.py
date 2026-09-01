from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


@dataclass
class _Entry(Generic[T]):
    value: T
    expires: float


class TTLCache(Generic[T]):
    def __init__(self, max_entries: int = 1024):
        self.max_entries = max_entries
        self._data: OrderedDict[str, _Entry[T]] = OrderedDict()
        self._lock = threading.RLock()

    def get(self, key: str) -> T | None:
        now = time.monotonic()
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if entry.expires <= now:
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return entry.value

    def set(self, key: str, value: T, ttl: float) -> None:
        if ttl <= 0:
            return
        with self._lock:
            self._data[key] = _Entry(value, time.monotonic() + ttl)
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


@dataclass
class _Flight:
    event: threading.Event
    result: object = None
    error: BaseException | None = None
    waiters: int = 0


class SingleFlight:
    def __init__(self):
        self._lock = threading.Lock()
        self._flights: dict[str, _Flight] = {}

    def run(self, key: str, fn: Callable[[], T]) -> T:
        with self._lock:
            flight = self._flights.get(key)
            if flight is None:
                flight = _Flight(threading.Event(), waiters=1)
                self._flights[key] = flight
                leader = True
            else:
                flight.waiters += 1
                leader = False
        if leader:
            try:
                flight.result = fn()
            except BaseException as exc:
                flight.error = exc
            finally:
                flight.event.set()
        else:
            flight.event.wait()
        try:
            if flight.error is not None:
                raise flight.error
            return flight.result  # type: ignore[return-value]
        finally:
            with self._lock:
                flight.waiters -= 1
                if flight.waiters == 0:
                    self._flights.pop(key, None)
