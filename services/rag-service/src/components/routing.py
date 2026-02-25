"""
Domain Routing Component
========================

Converts TransformersZeroShotTextRouter output to a Qdrant filter.

Pipeline flow:
  Query → TransformersZeroShotTextRouter → DomainFilterBuilder → SecureRetriever
"""

from haystack import component

from ..prompts import DOMAIN_LABELS


@component
class DomainFilterBuilder:
    """
    Bridges TransformersZeroShotTextRouter → SecureRetriever.
    Receives the router's named keyword outputs and builds a Qdrant filter dict.
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
        TransformersZeroShotTextRouter routes query text to exactly one label output.
        DomainFilterBuilder determines which label fired and builds a Qdrant filter.
        """
        if explicit_domain and explicit_domain != "all":
            query = hr_policy or technical or contracts or general
            detected_domain: str | None = explicit_domain
        elif hr_policy:
            query, detected_domain = hr_policy, "hr_policy"
        elif technical:
            query, detected_domain = technical, "technical"
        elif contracts:
            query, detected_domain = contracts, "contracts"
        else:
            query, detected_domain = general, None  # search all domains

        domain_filter = (
            {"key": self.filter_field, "match": {"value": detected_domain}}
            if detected_domain
            else None
        )

        return {
            "query": query or "",
            "domain_filter": domain_filter,
            "detected_domain": detected_domain or "all",
        }


__all__ = ["DomainFilterBuilder", "DOMAIN_LABELS"]
