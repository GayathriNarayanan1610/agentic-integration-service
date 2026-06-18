"""Retry policy for calls to external systems.

Transient faults (network blips, timeouts, 5xx, 429) are retried with
exponential backoff + jitter. Client errors (4xx other than 429) are NOT
retried — they will never succeed and retrying just hides the bug.
"""
from __future__ import annotations

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .config import settings
from .logging_config import get_logger

logger = get_logger(__name__)


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TransportError, httpx.TimeoutException)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code >= 500 or code == 429
    return False


def _log_retry(retry_state) -> None:  # pragma: no cover - logging only
    logger.warning(
        "retrying external call",
        extra={
            "attempt": retry_state.attempt_number,
            "callable": getattr(retry_state.fn, "__name__", "?"),
        },
    )


def with_retry(fn):
    """Decorate an async callable so transient failures are retried."""
    return retry(
        reraise=True,
        stop=stop_after_attempt(settings.max_retries),
        wait=wait_exponential_jitter(
            initial=settings.retry_base_delay, max=settings.retry_max_delay
        ),
        retry=retry_if_exception(_is_transient),
        before_sleep=_log_retry,
    )(fn)
