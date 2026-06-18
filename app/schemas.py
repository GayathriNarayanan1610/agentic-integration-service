"""Request/response schemas for the public API."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SupportRequest(BaseModel):
    external_id: str = Field(..., description="Stable id from the source system; used as the idempotency key.")
    customer_id: str = Field(..., description="CRM customer id, e.g. C-1001.")
    subject: str
    message: str
    channel: str = "email"


class RunRequest(BaseModel):
    support_request: SupportRequest


class ApprovalDecision(BaseModel):
    approved: bool
    approver: str = Field(..., description="Who is making the decision (for the audit trail).")
    note: str | None = None


class RunResponse(BaseModel):
    run_id: str
    status: str  # "completed" | "awaiting_approval"
    summary: str | None = None
    tickets: list[dict[str, Any]] = []
    approval: dict[str, Any] | None = None
