"""The agent graph.

  START -> agent -> (tools | approval | END)
           tools    -> agent
           approval -> agent

The LLM (the `agent` node) chooses which tool to call; nothing is hard-coded.
Safe reads go through `tools`. The risky write (create_ticket) goes through
`approval`, which pauses the graph with interrupt() when the account value is
above the threshold and waits for a human decision before acting.

Note on the resume semantics: when a node calls interrupt() and is later
resumed, LangGraph re-executes that node from the top. So `approval` performs
no non-idempotent side effects before the interrupt — the approval record is an
idempotent upsert and the "requested" audit line is written only on first entry.
"""
from __future__ import annotations

import json
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import interrupt

from .. import audit
from ..config import settings
from ..context import set_run_id
from ..policy import requires_approval
from ..services import tickets
from .llm import get_llm
from .prompts import SYSTEM_PROMPT
from .tools import TOOLS, TOOLS_BY_NAME, create_ticket

llm = get_llm()


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]


def _rid(config) -> str:
    return config["configurable"]["thread_id"]


async def agent_node(state: AgentState, config) -> dict:
    set_run_id(_rid(config))
    messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
    ai: AIMessage = await llm.ainvoke(messages)
    if ai.tool_calls:
        await audit.record(
            "llm_decision",
            data={"tool_calls": [{"name": tc["name"], "args": tc["args"]} for tc in ai.tool_calls]},
        )
    else:
        await audit.record("llm_final", data={"content": ai.content})
    return {"messages": [ai]}


def route_after_agent(state: AgentState) -> str:
    last = state["messages"][-1]
    tool_calls = getattr(last, "tool_calls", None)
    if not tool_calls:
        return END
    names = {tc["name"] for tc in tool_calls}
    if "create_ticket" in names:
        return "approval"
    return "tools"


async def tools_node(state: AgentState, config) -> dict:
    """Execute safe (read-only) tools with audit around every call."""
    set_run_id(_rid(config))
    last = state["messages"][-1]
    out: list[ToolMessage] = []
    for tc in last.tool_calls:
        if tc["name"] == "create_ticket":
            continue  # handled by the approval node
        await audit.record("tool_call", tc["name"], {"input": tc["args"]})
        try:
            result = await TOOLS_BY_NAME[tc["name"]].ainvoke(tc["args"])
            await audit.record("tool_result", tc["name"], {"output": result})
        except Exception as exc:  # surfaced back to the LLM as a tool result
            result = {"error": str(exc)}
            await audit.record("tool_error", tc["name"], {"error": str(exc)})
        out.append(
            ToolMessage(content=json.dumps(result, default=str), name=tc["name"], tool_call_id=tc["id"])
        )
    return {"messages": out}


async def approval_node(state: AgentState, config) -> dict:
    """Create tickets, gating high-value accounts behind human approval."""
    set_run_id(_rid(config))
    run_id = _rid(config)
    last = state["messages"][-1]
    out: list[ToolMessage] = []

    for tc in last.tool_calls:
        if tc["name"] != "create_ticket":
            continue
        args = tc["args"]
        value = float(args.get("estimated_value", 0.0))

        if requires_approval(value, settings.high_value_threshold):
            reason = f"estimated_value {value} >= threshold {settings.high_value_threshold}"
            newly = await tickets.upsert_approval(
                run_id=run_id, tool_call_id=tc["id"], tool_name="create_ticket",
                proposal=args, reason=reason,
            )
            if newly:
                await audit.record("approval_requested", "create_ticket", {"proposal": args, "reason": reason})

            # Pause the graph until a human decides. On resume, interrupt()
            # returns the decision dict instead of pausing again.
            decision = interrupt(
                {"type": "approval_request", "tool_call_id": tc["id"], "proposal": args, "reason": reason}
            )
            approved = bool(decision.get("approved"))
            approver = decision.get("approver", "unknown")
            note = decision.get("note")
            await tickets.resolve_approval(
                run_id=run_id, tool_call_id=tc["id"], approved=approved, approver=approver, note=note,
            )
            await audit.record(
                "approval_resolved", "create_ticket",
                {"approved": approved, "approver": approver, "note": note},
            )
            if not approved:
                rejected = {
                    "status": "rejected",
                    "ticket_id": None,
                    "detail": f"Ticket creation rejected by {approver}; escalated to a human owner.",
                }
                out.append(
                    ToolMessage(content=json.dumps(rejected), name="create_ticket", tool_call_id=tc["id"])
                )
                continue

        # Low-value (auto) or approved high-value: create idempotently.
        await audit.record("tool_call", "create_ticket", {"input": args})
        try:
            result = await create_ticket.ainvoke(args)
            await audit.record("tool_result", "create_ticket", {"output": result})
        except Exception as exc:
            result = {"error": str(exc)}
            await audit.record("tool_error", "create_ticket", {"error": str(exc)})
        out.append(
            ToolMessage(content=json.dumps(result, default=str), name="create_ticket", tool_call_id=tc["id"])
        )

    return {"messages": out}


def build_graph():
    g = StateGraph(AgentState)
    g.add_node("agent", agent_node)
    g.add_node("tools", tools_node)
    g.add_node("approval", approval_node)

    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", route_after_agent, {"tools": "tools", "approval": "approval", END: END})
    g.add_edge("tools", "agent")
    g.add_edge("approval", "agent")

    # MemorySaver keeps interrupted runs resumable in-process. For multi-replica
    # production, swap in AsyncPostgresSaver (langgraph-checkpoint-postgres) so a
    # pending approval survives restarts and can be resumed by any instance.
    return g.compile(checkpointer=MemorySaver())


graph = build_graph()
