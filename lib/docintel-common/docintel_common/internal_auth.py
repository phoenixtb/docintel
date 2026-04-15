"""
HMAC-based inter-service authentication.

The API Gateway signs every forwarded request with an HMAC-SHA256 token using
INTERNAL_GATEWAY_SECRET. Backend services (rag-service, ingestion-service) verify
this token before trusting any gateway-injected X-* headers.

Message format: "{request_id}:{tenant_id}:{user_id}"
"""

import hashlib
import hmac as _hmac
import os


def compute_internal_token(request_id: str, tenant_id: str, user_id: str, secret: str) -> str:
    """Compute the expected HMAC-SHA256 token for a given request context."""
    message = f"{request_id}:{tenant_id}:{user_id}".encode()
    return _hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_internal_token(
    token: str,
    request_id: str,
    tenant_id: str,
    user_id: str,
    secret: str,
) -> bool:
    """
    Verify that the X-Internal-Service-Token matches the expected HMAC.

    Uses constant-time comparison to prevent timing attacks.
    Returns False (rather than raising) so callers can choose the response.
    """
    if not token or not secret:
        return False
    expected = compute_internal_token(request_id, tenant_id, user_id, secret)
    return _hmac.compare_digest(token, expected)


def compute_service_token(tenant_id: str, secret: str) -> str:
    """
    Compute a token for direct service-to-service calls (no user/request context).

    Uses the same HMAC function as gateway tokens but with empty request_id and
    user_id, so the receiving service can validate both paths identically:
      - gateway token:  HMAC(requestId:tenantId:userId, secret)
      - service token:  HMAC("":tenantId:"",            secret)
    """
    return compute_internal_token("", tenant_id, "", secret)


def get_internal_secret() -> str:
    """Read INTERNAL_GATEWAY_SECRET from environment."""
    return os.environ.get("INTERNAL_GATEWAY_SECRET", "")
