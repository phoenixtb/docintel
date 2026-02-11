"""
Domain Router Components
========================

Domain classification and filter building for document-type routing.
Uses Haystack's built-in TransformersZeroShotTextRouter.
"""

from haystack import component
import litellm
import os


# Import from centralized prompts
from src.prompts import DOMAIN_LABELS


@component
class DomainFilterBuilder:
    """
    Converts router classification output to Qdrant filter.
    Bridges TransformersZeroShotTextRouter → SecureRetriever.

    Pipeline flow:
      Query → Router → DomainFilterBuilder → SecureRetriever
                ↓              ↓
           (classifies)   (builds filter)
    """

    def __init__(self, filter_field: str = "document_type"):
        self.filter_field = filter_field

    @component.output_types(
        query=str,
        domain_filter=dict | None,
        detected_domain=str,
    )
    def run(
        self,
        hr_policy: str | None = None,
        technical: str | None = None,
        contracts: str | None = None,
        general: str | None = None,
        explicit_domain: str | None = None,
    ) -> dict:
        """
        Receives routed text from TransformersZeroShotTextRouter.
        Router outputs to exactly one of: hr_policy, technical, contracts, general

        Args:
            hr_policy: Query text if classified as HR policy
            technical: Query text if classified as technical
            contracts: Query text if classified as contracts
            general: Query text if classified as general/unknown
            explicit_domain: User-specified domain (overrides auto-detection)
        """
        # Determine which output received the query
        if explicit_domain and explicit_domain != "all":
            query = hr_policy or technical or contracts or general
            detected_domain = explicit_domain
        elif hr_policy:
            query = hr_policy
            detected_domain = "hr_policy"
        elif technical:
            query = technical
            detected_domain = "technical"
        elif contracts:
            query = contracts
            detected_domain = "contracts"
        else:
            query = general
            detected_domain = None  # No filter, search all domains

        # Build Qdrant filter (None means search all)
        domain_filter = None
        if detected_domain:
            domain_filter = {
                "key": self.filter_field,
                "match": {"value": detected_domain},
            }

        return {
            "query": query or "",
            "domain_filter": domain_filter,
            "detected_domain": detected_domain or "all",
        }


@component
class QueryExpander:
    """
    Expands user query with synonyms and related terms.
    Implements Query Transformation for vocabulary gap mitigation.

    Addresses vocabulary gap problem: "WFH" vs "remote work"
    """

    def __init__(self, llm_model: str | None = None, enabled: bool = True):
        self.llm_model = llm_model or os.getenv("LITELLM_EXPANSION_MODEL", "ollama/qwen3:1.7b")
        self.enabled = enabled
        self.api_base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    @component.output_types(
        original_query=str,
        expanded_query=str,
        search_terms=list[str],
    )
    def run(self, query: str) -> dict:
        if not self.enabled:
            return {
                "original_query": query,
                "expanded_query": query,
                "search_terms": [query],
            }

        prompt = f"""Given this search query, generate 2-3 alternative phrasings 
or related terms that might appear in documents. Keep it brief.

Query: {query}

Alternative terms (comma-separated):"""

        try:
            response = litellm.completion(
                model=self.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=50,
                api_base=self.api_base if self.llm_model.startswith("ollama/") else None,
            )

            alternatives = response.choices[0].message.content.strip()
            terms = [t.strip() for t in alternatives.split(",")]

            # Combine original + expansions
            expanded = f"{query} {' '.join(terms)}"

            return {
                "original_query": query,
                "expanded_query": expanded,
                "search_terms": [query] + terms,
            }
        except Exception:
            # Fallback to original query on error
            return {
                "original_query": query,
                "expanded_query": query,
                "search_terms": [query],
            }


@component
class CostTracker:
    """
    Tracks LLM usage costs per query/tenant.
    Uses LiteLLM's built-in cost tracking.
    """

    def __init__(self):
        self.costs: dict[str, dict] = {}

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
        tokens = {"prompt": 0, "completion": 0}

        if litellm_response:
            # Extract from LiteLLM response
            usage = litellm_response.get("usage", {})
            tokens = {
                "prompt": usage.get("prompt_tokens", 0),
                "completion": usage.get("completion_tokens", 0),
            }

            # LiteLLM provides cost calculation
            try:
                cost = litellm.completion_cost(litellm_response) or 0.0
            except Exception:
                cost = 0.0

        # Aggregate per tenant
        if tenant_id not in self.costs:
            self.costs[tenant_id] = {"total_cost": 0.0, "query_count": 0}

        self.costs[tenant_id]["total_cost"] += cost
        self.costs[tenant_id]["query_count"] += 1

        return {
            "response": response,
            "cost_usd": cost,
            "tokens_used": tokens,
        }

    def get_tenant_costs(self, tenant_id: str) -> dict:
        """Get accumulated costs for a tenant."""
        return self.costs.get(tenant_id, {"total_cost": 0.0, "query_count": 0})
