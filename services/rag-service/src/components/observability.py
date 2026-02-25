"""
Observability Components
=========================

Cost tracking per tenant using LiteLLM's built-in cost calculation.

Usage: wire CostTracker after the LLM component to accumulate per-tenant spend.
"""

import logging

import litellm
from haystack import component

logger = logging.getLogger(__name__)


@component
class CostTracker:
    """
    Accumulates LLM usage costs per tenant.
    In-memory store — suitable for single-replica deployments.
    For multi-replica, push costs to a shared store (Redis / DB).
    """

    def __init__(self) -> None:
        self._costs: dict[str, dict] = {}

    @component.output_types(
        response=str,
        cost_usd=float,
        tokens_used=dict,
    )
    def run(
        self,
        response: str,
        tenant_id: str,
        litellm_response: dict | None = None,
    ) -> dict:
        cost = 0.0
        tokens: dict = {"prompt": 0, "completion": 0}

        if litellm_response:
            usage = litellm_response.get("usage", {})
            tokens = {
                "prompt": usage.get("prompt_tokens", 0),
                "completion": usage.get("completion_tokens", 0),
            }
            try:
                cost = litellm.completion_cost(litellm_response) or 0.0
            except Exception:
                cost = 0.0

        bucket = self._costs.setdefault(tenant_id, {"total_cost": 0.0, "query_count": 0})
        bucket["total_cost"] += cost
        bucket["query_count"] += 1

        return {"response": response, "cost_usd": cost, "tokens_used": tokens}

    def get_tenant_costs(self, tenant_id: str) -> dict:
        return self._costs.get(tenant_id, {"total_cost": 0.0, "query_count": 0})


__all__ = ["CostTracker"]
