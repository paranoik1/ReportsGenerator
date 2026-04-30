"""Rate limiter для контроля частоты запросов к LLM API."""

import threading
import time

import structlog

logger = structlog.get_logger(__name__)


class RateLimiter:
    """
    Контролирует частоту запросов к API.
    Использует простую задержку между запросами.
    """

    def __init__(self, min_delay: float = 1.0) -> None:
        self.min_delay = min_delay  # Минимальная задержка между запросами (сек)
        self._lock = threading.Lock()
        self._last_call_time = 0.0

    def acquire(self) -> None:
        """Ждёт, пока можно будет сделать запрос."""
        with self._lock:
            elapsed = time.time() - self._last_call_time
            if elapsed < self.min_delay:
                sleep_time = self.min_delay - elapsed
                time.sleep(sleep_time)
            self._last_call_time = time.time()
