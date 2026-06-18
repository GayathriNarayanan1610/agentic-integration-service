"""The three tools the agent can call.

Tools are thin, typed wrappers over the service layer. Cross-cutting concerns
live elsewhere on purpose:
  * retries          -> in the service clients (closest to the failure)
  * audit logging    -> in the graph nodes (around every dispatch)
  * idempotency      -> in the ticket repository (the DB constraint)
  * approval gating  -> in the graph's approval node (needs interrupt())
This keeps each tool trivial to read and reuse.
"""
from __future__ import annotations

from langchain_core.tools import tool

from ..context import get_run_id
from ..services import crm, enrichment, tickets


@tool
async def lookup_customer(customer_id: str) -> dict:
    """Look up a customer record in the CRM by customer_id (e.g. 'C-1001')."""
    return await crm.lookup_customer(customer_id)


@tool
async def enrich_company(domain: str) -> dict:
    """Enrich a company by domain; returns firmographics and estimated_account_value."""
    return await enrichment.enrich_company(domain)


@tool
async def create_ticket(
    idempotency_key: str,
    customer_id: str,
    subject: str,
    description: str,
    priority: str,
    estimated_value: float,
) -> dict:
    """Create a support ticket. Idempotent on idempotency_key."""
    ticket, created = await tickets.create_ticket_idempotent(
        run_id=get_run_id(),
        idempotency_key=idempotency_key,
        customer_id=customer_id,
        subject=subject,
        description=description,
        priority=priority,
        value=estimated_value,
    )
    return {
        "ticket_id": ticket["id"],
        "status": ticket["status"],
        "created": created,  # False means the idempotency key already existed
    }


TOOLS = [lookup_customer, enrich_company, create_ticket]
TOOLS_BY_NAME = {t.name: t for t in TOOLS}
