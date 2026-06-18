"""Append-only audit trail.

Every meaningful step in a run is written here: the LLM's decision, each tool
call with its inputs and outputs, approval requests and their resolution, and
ticket creation. This is the artifact that lets an enterprise *trust* an agent
— you can reconstruct exactly what it did and why.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from .context import get_run_id
from .db import async_session
from .logging_config import get_logger
from .models import AuditEvent

logger = get_logger(__name__)


async def record(
    event_type: str,
    tool_name: str | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    run_id = get_run_id()
    async with async_session() as session:
        session.add(
            AuditEvent(
                run_id=uuid.UUID(run_id),
                event_type=event_type,
                tool_name=tool_name,
                data=data or {},
            )
        )
        await session.commit()
    logger.info("audit", extra={"event_type": event_type, "tool_name": tool_name})


async def list_events(run_id: str) -> list[dict[str, Any]]:
    async with async_session() as session:
        rows = (
            await session.execute(
                select(AuditEvent)
                .where(AuditEvent.run_id == uuid.UUID(run_id))
                .order_by(AuditEvent.ts.asc())
            )
        ).scalars().all()
    return [
        {
            "ts": e.ts.isoformat() if e.ts else None,
            "event_type": e.event_type,
            "tool_name": e.tool_name,
            "data": e.data,
        }
        for e in rows
    ]
