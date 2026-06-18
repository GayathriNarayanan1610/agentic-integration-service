"""LLM provider.

`azure` mode uses Azure OpenAI via langchain-openai. `mock` mode is a
deterministic planner that walks the same lookup -> enrich -> create_ticket ->
summarise path, so the whole service (and its tests) runs with no credentials
and no network. Being able to exercise the agent offline is a real production
asset, not just a demo convenience.
"""
from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from ..config import settings
from ..policy import suggest_priority
from .tools import TOOLS


def _first_human_json(messages: list) -> dict:
    for m in messages:
        if isinstance(m, HumanMessage):
            try:
                return json.loads(m.content)
            except (ValueError, TypeError):
                return {}
    return {}


def _tool_result(messages: list, name: str) -> dict | None:
    for m in messages:
        if isinstance(m, ToolMessage) and getattr(m, "name", None) == name:
            try:
                return json.loads(m.content)
            except (ValueError, TypeError):
                return None
    return None


class MockPlanner:
    """A stand-in for a tool-calling chat model. Same interface: bind_tools + ainvoke."""

    def bind_tools(self, _tools):
        return self

    async def ainvoke(self, messages: list, *args, **kwargs) -> AIMessage:
        support = _first_human_json(messages)

        if _tool_result(messages, "lookup_customer") is None:
            return AIMessage(
                content="",
                tool_calls=[{
                    "name": "lookup_customer",
                    "args": {"customer_id": support.get("customer_id", "")},
                    "id": "call_lookup",
                    "type": "tool_call",
                }],
            )

        if _tool_result(messages, "enrich_company") is None:
            crm = _tool_result(messages, "lookup_customer") or {}
            return AIMessage(
                content="",
                tool_calls=[{
                    "name": "enrich_company",
                    "args": {"domain": crm.get("domain", "")},
                    "id": "call_enrich",
                    "type": "tool_call",
                }],
            )

        if _tool_result(messages, "create_ticket") is None:
            enrich = _tool_result(messages, "enrich_company") or {}
            value = float(enrich.get("estimated_account_value", 0.0))
            return AIMessage(
                content="",
                tool_calls=[{
                    "name": "create_ticket",
                    "args": {
                        "idempotency_key": support.get("external_id", ""),
                        "customer_id": support.get("customer_id", ""),
                        "subject": support.get("subject", ""),
                        "description": support.get("message", ""),
                        "priority": suggest_priority(value, settings.high_value_threshold),
                        "estimated_value": value,
                    },
                    "id": "call_ticket",
                    "type": "tool_call",
                }],
            )

        ticket = _tool_result(messages, "create_ticket") or {}
        enrich = _tool_result(messages, "enrich_company") or {}
        return AIMessage(
            content=(
                f"Created ticket {ticket.get('ticket_id')} for customer "
                f"{support.get('customer_id')} at priority "
                f"{suggest_priority(float(enrich.get('estimated_account_value', 0)), settings.high_value_threshold)} "
                f"(estimated account value {enrich.get('estimated_account_value')})."
            )
        )


def get_llm():
    if settings.llm_mode == "azure":
        from langchain_openai import AzureChatOpenAI

        return AzureChatOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            azure_deployment=settings.azure_openai_deployment,
            api_version=settings.azure_openai_api_version,
            temperature=settings.llm_temperature,
        ).bind_tools(TOOLS)
    return MockPlanner().bind_tools(TOOLS)
