"""Account enrichment (tool #2: external API).

Returns firmographics for a company domain, including an estimated account
value that the agent uses to set priority and that drives the approval gate.

Defaults to a deterministic mock so the demo is reproducible. Point
ENRICHMENT_API_URL at any real enrichment endpoint (expects ?domain=) to use it
for real; the call is retried on transient failure.
"""
from __future__ import annotations

import hashlib

import httpx

from ..config import settings
from ..retry import with_retry

# A couple of domains are pinned to make the demo deterministic:
#   acme.io   -> high value  -> triggers human approval
#   globex.com-> low value   -> auto-creates
#   initech.co-> high value  -> triggers human approval
_PINNED_VALUE: dict[str, float] = {
    "acme.io": 50_000.0,
    "globex.com": 2_500.0,
    "initech.co": 18_000.0,
}


def _mock_value(domain: str) -> float:
    if domain in _PINNED_VALUE:
        return _PINNED_VALUE[domain]
    # Deterministic pseudo-value for any other domain.
    digest = int(hashlib.sha256(domain.encode()).hexdigest(), 16)
    return float(1_000 + digest % 60_000)


@with_retry
async def _remote_enrich(domain: str) -> dict:
    async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
        resp = await client.get(settings.enrichment_api_url, params={"domain": domain})
        resp.raise_for_status()
        return resp.json()


async def enrich_company(domain: str) -> dict:
    if settings.enrichment_api_url:
        data = await _remote_enrich(domain)
        data.setdefault("domain", domain)
        return data
    value = _mock_value(domain)
    return {
        "domain": domain,
        "company_name": domain.split(".")[0].title(),
        "employee_count": 50 + int(value // 1000),
        "industry": "Technology",
        "estimated_account_value": value,
        "data_source": "mock",
    }
