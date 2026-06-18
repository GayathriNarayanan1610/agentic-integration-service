"""Ticket system-of-record (tool #3: write) + approval bookkeeping.

The create is *idempotent*: it relies on a UNIQUE constraint on idempotency_key
and INSERT ... ON CONFLICT DO NOTHING, so a retried or duplicated action can
never create two tickets for the same source event. This is the single most
important guardrail for an agent that takes real-world actions.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..db import async_session
from ..models import ApprovalRequest, Ticket


def _ticket_dict(t: Ticket) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "run_id": str(t.run_id) if t.run_id else None,
        "idempotency_key": t.idempotency_key,
        "customer_id": t.customer_id,
        "subject": t.subject,
        "priority": t.priority,
        "value": t.value,
        "status": t.status,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


async def create_ticket_idempotent(
    *,
    run_id: str,
    idempotency_key: str,
    customer_id: str,
    subject: str,
    description: str,
    priority: str,
    value: float,
) -> tuple[dict[str, Any], bool]:
    """Insert a ticket if one does not already exist for this idempotency key.

    Returns (ticket, created). `created` is False when the key already existed,
    in which case the original ticket is returned unchanged.
    """
    async with async_session() as session:
        stmt = (
            pg_insert(Ticket)
            .values(
                run_id=uuid.UUID(run_id),
                idempotency_key=idempotency_key,
                customer_id=customer_id,
                subject=subject,
                description=description,
                priority=priority,
                value=value,
                status="open",
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
        )
        result = await session.execute(stmt)
        created = result.rowcount == 1
        await session.commit()

        row = (
            await session.execute(
                select(Ticket).where(Ticket.idempotency_key == idempotency_key)
            )
        ).scalar_one()
        return _ticket_dict(row), created


async def get_ticket(ticket_id: str) -> dict[str, Any] | None:
    async with async_session() as session:
        row = (
            await session.execute(select(Ticket).where(Ticket.id == uuid.UUID(ticket_id)))
        ).scalar_one_or_none()
    return _ticket_dict(row) if row else None


async def list_tickets_for_run(run_id: str) -> list[dict[str, Any]]:
    async with async_session() as session:
        rows = (
            await session.execute(select(Ticket).where(Ticket.run_id == uuid.UUID(run_id)))
        ).scalars().all()
    return [_ticket_dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Approval bookkeeping (durable record behind the human-in-the-loop gate)
# --------------------------------------------------------------------------- #
async def upsert_approval(
    *, run_id: str, tool_call_id: str, tool_name: str, proposal: dict, reason: str
) -> bool:
    """Idempotently record that an approval was requested.

    Returns True only when a new record was inserted. Idempotent because the
    approval node is re-executed when the graph resumes; ON CONFLICT DO NOTHING
    keeps the original 'pending' record intact and returns False on re-entry.
    """
    async with async_session() as session:
        stmt = (
            pg_insert(ApprovalRequest)
            .values(
                run_id=uuid.UUID(run_id),
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                proposal=proposal,
                reason=reason,
                status="pending",
            )
            .on_conflict_do_nothing(index_elements=["run_id", "tool_call_id"])
        )
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount == 1


async def resolve_approval(
    *, run_id: str, tool_call_id: str, approved: bool, approver: str, note: str | None
) -> None:
    async with async_session() as session:
        await session.execute(
            update(ApprovalRequest)
            .where(
                ApprovalRequest.run_id == uuid.UUID(run_id),
                ApprovalRequest.tool_call_id == tool_call_id,
            )
            .values(
                status="approved" if approved else "rejected",
                approver=approver,
                note=note,
                resolved_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()


async def list_pending_approvals() -> list[dict[str, Any]]:
    async with async_session() as session:
        rows = (
            await session.execute(
                select(ApprovalRequest)
                .where(ApprovalRequest.status == "pending")
                .order_by(ApprovalRequest.created_at.asc())
            )
        ).scalars().all()
    return [
        {
            "run_id": str(r.run_id),
            "tool_call_id": r.tool_call_id,
            "tool_name": r.tool_name,
            "proposal": r.proposal,
            "reason": r.reason,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
