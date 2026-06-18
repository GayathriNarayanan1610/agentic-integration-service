"""Per-request context.

The agent run id is propagated via a ContextVar so that tools, services and the
audit logger can stamp every record with it without threading the value through
every function signature. FastAPI runs each request in its own task, so the
value never leaks between concurrent runs.
"""
from __future__ import annotations

from contextvars import ContextVar

RUN_ID: ContextVar[str] = ContextVar("run_id", default="-")


def get_run_id() -> str:
    return RUN_ID.get()


def set_run_id(run_id: str) -> None:
    RUN_ID.set(run_id)
