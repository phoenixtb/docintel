"""
Re-export of the shared ModelProfileResolver from docintel_common.

The implementation moved to lib/docintel-common so ingestion-service can use
the same resolver (and shared TTL cache) for VLM sampling parameters.

Existing imports (`from src.components.model_profile_resolver import ...`)
continue to work via this shim; no call-site changes needed.
"""

from docintel_common.model_profile_resolver import (  # noqa: F401
    BUILTIN_PROFILES,
    ModelProfileResolver,
    ModelSamplingParams,
    _best_match,
    _builtin_for,
    _match_pattern,
    _merge,
    _row_to_params,
    infer_kind,
)
