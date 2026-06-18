"""CRM system-of-record (tool #1: read).

Defaults to a small in-memory dataset so the project runs with zero external
credentials. Set HUBSPOT_TOKEN to switch to a real HubSpot lookup — the call
is wrapped in the same retry policy as every other external dependency.
"""
from __future__ import annotations

import httpx

from ..config import settings
from ..retry import with_retry


class CustomerNotFound(Exception):
    pass


# Deterministic mock customers. Two of them resolve (via enrichment) to
# high-value accounts so the human-in-the-loop path is easy to demo.
_MOCK_CRM: dict[str, dict] = {
    "C-1001": {"name": "Acme Corp", "email": "ops@acme.io", "domain": "acme.io", "tier": "enterprise"},
    "C-2002": {"name": "Globex Ltd", "email": "support@globex.com", "domain": "globex.com", "tier": "smb"},
    "C-3003": {"name": "Initech", "email": "it@initech.co", "domain": "initech.co", "tier": "mid"},
}


@with_retry
async def _hubspot_lookup(customer_id: str) -> dict:
    headers = {"Authorization": f"Bearer {settings.hubspot_token}"}
    url = f"https://api.hubapi.com/crm/v3/objects/contacts/{customer_id}"
    async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
        resp = await client.get(url, headers=headers, params={"properties": "email,company,website"})
        resp.raise_for_status()
        props = resp.json().get("properties", {})
    return {
        "name": props.get("company") or customer_id,
        "email": props.get("email"),
        "domain": (props.get("website") or "").replace("https://", "").replace("http://", "").strip("/"),
        "tier": "unknown",
    }


async def lookup_customer(customer_id: str) -> dict:
    """Return the CRM record for a customer, or raise CustomerNotFound."""
    if settings.hubspot_token:
        record = await _hubspot_lookup(customer_id)
    else:
        record = _MOCK_CRM.get(customer_id)
        if record is None:
            raise CustomerNotFound(f"customer {customer_id} not found in CRM")
    return {"customer_id": customer_id, **record}
