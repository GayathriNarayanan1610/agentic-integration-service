"""FastAPI surface for the Agentic Integration Service.

Endpoints:
  POST /agent/runs                  - start a run from a support request
  POST /agent/runs/{run_id}/approve - resolve a human-in-the-loop approval
  GET  /agent/runs/{run_id}/audit   - full audit trail for a run
  GET  /agent/approvals             - list pending approvals (the human queue)
  GET  /tickets/{ticket_id}         - fetch a ticket
  GET  /healthz, /readyz            - liveness / readiness
"""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command
from sqlalchemy import text

from . import audit
from .agent.graph import graph
from .context import set_run_id
from .db import async_session, init_db
from .logging_config import configure_logging, get_logger
from .schemas import ApprovalDecision, RunRequest, RunResponse
from .services import tickets

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    await init_db()
    logger.info("service started")
    yield


app = FastAPI(title="Agentic Integration Service", version="1.0.0", lifespan=lifespan)


async def _interpret(run_id: str, config: dict) -> RunResponse:
    """Translate the current graph state into an API response.

    A run is paused for approval when its state has a pending `next` node; the
    interrupt payload is carried on that node's task.
    """
    state = await graph.aget_state(config)

    if state.next:  # paused — waiting on a human
        payload = None
        for task in state.tasks:
            interrupts = getattr(task, "interrupts", None)
            if interrupts:
                payload = getattr(interrupts[0], "value", None)
                break
        return RunResponse(run_id=run_id, status="awaiting_approval", approval=payload)

    await audit.record("run_completed")
    messages = state.values.get("messages", [])
    summary = None
    for message in reversed(messages):
        if isinstance(message, AIMessage) and not getattr(message, "tool_calls", None):
            summary = message.content
            break

    # Surface the ticket(s) the agent acted on in THIS run, read from the
    # create_ticket tool results. This correctly reflects idempotent re-runs,
    # where the agent references a pre-existing ticket rather than a new row.
    tickets_out: list[dict] = []
    seen: set[str] = set()
    for message in messages:
        if isinstance(message, ToolMessage) and getattr(message, "name", None) == "create_ticket":
            try:
                data = json.loads(message.content)
            except (ValueError, TypeError):
                continue
            ticket_id = data.get("ticket_id")
            if ticket_id and ticket_id not in seen:
                seen.add(ticket_id)
                ticket = await tickets.get_ticket(ticket_id)
                if ticket:
                    tickets_out.append(ticket)
    return RunResponse(run_id=run_id, status="completed", summary=summary, tickets=tickets_out)


@app.post("/agent/runs", response_model=RunResponse)
async def create_run(req: RunRequest) -> RunResponse:
    run_id = str(uuid.uuid4())
    set_run_id(run_id)
    config = {"configurable": {"thread_id": run_id}}
    await audit.record("run_started", data={"support_request": req.support_request.model_dump()})
    init_state = {"messages": [HumanMessage(content=req.support_request.model_dump_json())]}
    await graph.ainvoke(init_state, config)
    return await _interpret(run_id, config)


@app.post("/agent/runs/{run_id}/approve", response_model=RunResponse)
async def resolve_approval(run_id: str, decision: ApprovalDecision) -> RunResponse:
    set_run_id(run_id)
    config = {"configurable": {"thread_id": run_id}}
    try:
        state = await graph.aget_state(config)
    except Exception:  # pragma: no cover
        state = None
    if not state or not state.next:
        raise HTTPException(status_code=404, detail="No paused run to resume for this run_id.")
    await graph.ainvoke(Command(resume=decision.model_dump()), config)
    return await _interpret(run_id, config)


@app.get("/agent/runs/{run_id}/audit")
async def get_audit(run_id: str) -> dict:
    return {"run_id": run_id, "events": await audit.list_events(run_id)}


@app.get("/agent/approvals")
async def get_pending_approvals() -> dict:
    return {"pending": await tickets.list_pending_approvals()}


@app.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str) -> dict:
    ticket = await tickets.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="ticket not found")
    return ticket


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict:
    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=503, detail=f"db not ready: {exc}")
