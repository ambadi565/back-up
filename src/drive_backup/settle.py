"""Wait until a file stops changing before uploading it (with a maximum wait)."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable, Hashable


class Settler:
    def __init__(self, debounce_s: float = 5.0, max_wait_s: float = 1800.0,
                 clock: Callable[[], float] = time.monotonic):
        self.debounce_s = debounce_s
        self.max_wait_s = max_wait_s
        self._clock = clock
        self._pending: dict[Hashable, tuple[float, float]] = {}  # key -> (first, last)
        self._lock = threading.Lock()

    def touch(self, key: Hashable) -> None:
        now = self._clock()
        with self._lock:
            first, _ = self._pending.get(key, (now, now))
            self._pending[key] = (first, now)

    def ready(self) -> list[tuple[Hashable, bool]]:
        now = self._clock()
        out: list[tuple[Hashable, bool]] = []
        with self._lock:
            for key, (first, last) in list(self._pending.items()):
                if now - last >= self.debounce_s:
                    out.append((key, False))
                elif now - first >= self.max_wait_s:
                    out.append((key, True))
                else:
                    continue
                del self._pending[key]
        return out

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)
