"""Unit tests that need no database or network.

These cover the deterministic guardrail logic: the approval threshold and the
transient-vs-permanent retry decision. They run anywhere, instantly.
"""
from __future__ import annotations

import httpx
import pytest

from app.policy import requires_approval, suggest_priority
from app.retry import _is_transient


def test_requires_approval_threshold():
    assert requires_approval(10_000, 10_000) is True   # boundary is inclusive
    assert requires_approval(50_000, 10_000) is True
    assert requires_approval(9_999, 10_000) is False


def test_suggest_priority():
    assert suggest_priority(50_000, 10_000) == "urgent"
    assert suggest_priority(3_000, 10_000) == "high"
    assert suggest_priority(500, 10_000) == "normal"


def test_retry_classifies_transient_vs_permanent():
    req = httpx.Request("GET", "https://x")
    assert _is_transient(httpx.ConnectError("boom", request=req)) is True
    assert _is_transient(httpx.ReadTimeout("slow", request=req)) is True
    assert _is_transient(
        httpx.HTTPStatusError("server", request=req, response=httpx.Response(503, request=req))
    ) is True
    assert _is_transient(
        httpx.HTTPStatusError("rate", request=req, response=httpx.Response(429, request=req))
    ) is True
    # 4xx (other than 429) must NOT be retried
    assert _is_transient(
        httpx.HTTPStatusError("bad", request=req, response=httpx.Response(400, request=req))
    ) is False


@pytest.mark.asyncio
async def test_with_retry_recovers_after_transient_failures():
    from app.retry import with_retry

    calls = {"n": 0}

    @with_retry
    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("transient", request=httpx.Request("GET", "https://x"))
        return "ok"

    assert await flaky() == "ok"
    assert calls["n"] == 3  # failed twice, succeeded on the third attempt
