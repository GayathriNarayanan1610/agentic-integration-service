"""End-to-end tests of the whole agent in mock LLM mode.

Requires a reachable Postgres (DATABASE_URL). Skips cleanly if there isn't one,
so `pytest` still passes on a laptop without a DB. Under docker-compose:

    docker compose run --rm api pytest -q

Covers the three guardrails that make this project worth talking about:
  * idempotency      - re-submitting the same request never double-creates
  * human-in-the-loop- high-value tickets pause for approval (approve + reject)
  * audit trail      - every decision and tool call is recorded
"""
from __future__ import annotations

import uuid

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import text

from app.db import async_session, engine, init_db
from app.main import app


async def _db_available() -> bool:
    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.fixture
async def client():
    # pytest-asyncio gives each test its own event loop; drop any pooled
    # connections bound to a previous loop before this test connects.
    await engine.dispose()
    if not await _db_available():
        pytest.skip("Postgres not available (set DATABASE_URL / run under docker compose)")
    await init_db()
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _request(customer_id: str, external_id: str) -> dict:
    return {
        "support_request": {
            "external_id": external_id,
            "customer_id": customer_id,
            "subject": "Cannot log in",
            "message": "User reports repeated 500 errors on login.",
        }
    }


async def test_low_value_autocreates(client):
    # Globex (globex.com) is a low-value account -> no approval needed.
    ext = f"sr-{uuid.uuid4()}"
    resp = await client.post("/agent/runs", json=_request("C-2002", ext))
    body = resp.json()
    assert body["status"] == "completed"
    assert len(body["tickets"]) == 1
    assert body["tickets"][0]["priority"] in {"normal", "high"}


async def test_idempotent_resubmit_creates_no_duplicate(client):
    ext = f"sr-{uuid.uuid4()}"
    first = (await client.post("/agent/runs", json=_request("C-2002", ext))).json()
    second = (await client.post("/agent/runs", json=_request("C-2002", ext))).json()
    # Same idempotency key (external_id) -> same ticket, no duplicate.
    assert first["tickets"][0]["id"] == second["tickets"][0]["id"]


async def test_high_value_requires_approval_then_creates(client):
    # Acme (acme.io) is a high-value account -> must pause for approval.
    ext = f"sr-{uuid.uuid4()}"
    started = (await client.post("/agent/runs", json=_request("C-1001", ext))).json()
    assert started["status"] == "awaiting_approval"
    run_id = started["run_id"]

    # It should be visible in the human queue.
    pending = (await client.get("/agent/approvals")).json()["pending"]
    assert any(p["run_id"] == run_id for p in pending)

    # Approve it -> the ticket gets created.
    done = (
        await client.post(
            f"/agent/runs/{run_id}/approve",
            json={"approved": True, "approver": "ops-lead@corp", "note": "VIP, proceed"},
        )
    ).json()
    assert done["status"] == "completed"
    assert len(done["tickets"]) == 1

    events = (await client.get(f"/agent/runs/{run_id}/audit")).json()["events"]
    types = [e["event_type"] for e in events]
    assert "approval_requested" in types
    assert "approval_resolved" in types


async def test_high_value_rejected_creates_no_ticket(client):
    ext = f"sr-{uuid.uuid4()}"
    started = (await client.post("/agent/runs", json=_request("C-1001", ext))).json()
    run_id = started["run_id"]
    done = (
        await client.post(
            f"/agent/runs/{run_id}/approve",
            json={"approved": False, "approver": "ops-lead@corp", "note": "duplicate of INC-42"},
        )
    ).json()
    assert done["status"] == "completed"
    assert done["tickets"] == []  # rejected -> escalated, nothing created
