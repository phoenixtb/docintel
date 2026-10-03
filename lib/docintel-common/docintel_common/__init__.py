"""
docintel_common — shared utilities for DocIntel services.

Submodule imports (`from docintel_common.messaging import RedisStreamBus`)
work as normal. Top-level attribute access (`from docintel_common import
RedisStreamBus` / `docintel_common.RedisStreamBus`) is served lazily via
module __getattr__ (PEP 562): only the submodule actually needed is
imported.

This matters because some submodules pull in heavy ML dependencies
(transformers, torch for the domain classifier; psycopg2 for the model
profile resolver). A lightweight consumer that only needs the Redis Streams
bus (e.g. analytics-service-py) must not be forced to install/import those —
eager `from .domain import ...` at package-init time would do exactly that,
since importing any submodule always runs the package's __init__ first.
"""

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .device import detect_device
    from .domain import (
        DOMAIN_DESCRIPTIONS,
        DOMAIN_LABELS,
        ClassificationResult,
        DomainClassifier,
        get_domain_classifier,
    )
    from .internal_auth import (
        compute_internal_token,
        compute_service_token,
        get_internal_secret,
        verify_internal_token,
    )
    from .tracing import TraceContext, TraceLogFilter, configure_trace_logging
    from .errors import error_envelope, install_error_handlers
    from .messaging import (
        MessageBus,
        RedisStreamBus,
        TOPIC_FILES_AVAILABLE,
        TOPIC_DOCUMENTS_READY,
        TOPIC_INGESTION_COMPLETE,
        TOPIC_ANALYTICS_QUERY,
    )
    from .security import (
        CLASSIFICATION_ORDER,
        Classification,
        DocumentACL,
        RetrievalAuditEvent,
        UserContext,
        clearance_permits,
    )
    from .model_profile_resolver import (
        BUILTIN_PROFILES,
        ModelProfileResolver,
        ModelSamplingParams,
        infer_kind,
    )

__all__ = [
    "DOMAIN_LABELS",
    "DOMAIN_DESCRIPTIONS",
    "ClassificationResult",
    "DomainClassifier",
    "get_domain_classifier",
    "detect_device",
    # internal auth
    "compute_internal_token",
    "compute_service_token",
    "verify_internal_token",
    "get_internal_secret",
    # tracing
    "TraceContext",
    "TraceLogFilter",
    "configure_trace_logging",
    # error envelope
    "error_envelope",
    "install_error_handlers",
    # messaging
    "MessageBus",
    "RedisStreamBus",
    "TOPIC_FILES_AVAILABLE",
    "TOPIC_DOCUMENTS_READY",
    "TOPIC_INGESTION_COMPLETE",
    "TOPIC_ANALYTICS_QUERY",
    # security models
    "Classification",
    "CLASSIFICATION_ORDER",
    "clearance_permits",
    "DocumentACL",
    "UserContext",
    "RetrievalAuditEvent",
    # model profile resolver
    "BUILTIN_PROFILES",
    "ModelProfileResolver",
    "ModelSamplingParams",
    "infer_kind",
]

_ATTR_TO_SUBMODULE = {
    "detect_device": "device",
    "DOMAIN_DESCRIPTIONS": "domain",
    "DOMAIN_LABELS": "domain",
    "ClassificationResult": "domain",
    "DomainClassifier": "domain",
    "get_domain_classifier": "domain",
    "compute_internal_token": "internal_auth",
    "compute_service_token": "internal_auth",
    "get_internal_secret": "internal_auth",
    "verify_internal_token": "internal_auth",
    "TraceContext": "tracing",
    "TraceLogFilter": "tracing",
    "configure_trace_logging": "tracing",
    "error_envelope": "errors",
    "install_error_handlers": "errors",
    "MessageBus": "messaging",
    "RedisStreamBus": "messaging",
    "TOPIC_FILES_AVAILABLE": "messaging",
    "TOPIC_DOCUMENTS_READY": "messaging",
    "TOPIC_INGESTION_COMPLETE": "messaging",
    "TOPIC_ANALYTICS_QUERY": "messaging",
    "CLASSIFICATION_ORDER": "security",
    "Classification": "security",
    "DocumentACL": "security",
    "RetrievalAuditEvent": "security",
    "UserContext": "security",
    "clearance_permits": "security",
    "BUILTIN_PROFILES": "model_profile_resolver",
    "ModelProfileResolver": "model_profile_resolver",
    "ModelSamplingParams": "model_profile_resolver",
    "infer_kind": "model_profile_resolver",
}


def __getattr__(name: str):
    submodule_name = _ATTR_TO_SUBMODULE.get(name)
    if submodule_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    submodule = importlib.import_module(f".{submodule_name}", __name__)
    value = getattr(submodule, name)
    globals()[name] = value  # cache — subsequent access skips __getattr__
    return value


def __dir__():
    return sorted(__all__)
