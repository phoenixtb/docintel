#!/bin/bash
# Seed Test Data for DocIntel
# ============================
# Loads sample documents for testing

set -e

RAG_SERVICE_URL="${RAG_SERVICE_URL:-http://localhost:8000}"
TENANT_ID="${TENANT_ID:-demo}"

echo "================================================"
echo "Seeding DocIntel with Test Data"
echo "================================================"
echo "RAG Service: ${RAG_SERVICE_URL}"
echo "Tenant: ${TENANT_ID}"
echo ""

# =============================================================================
# Sample HR Policy Documents
# =============================================================================

echo "Loading HR Policy documents..."

curl -X POST "${RAG_SERVICE_URL}/index" \
  -H "Content-Type: application/json" \
  -d '{
    "document_id": "hr-vacation-policy",
    "tenant_id": "'${TENANT_ID}'",
    "content": "Vacation Policy: Full-time employees are entitled to 20 days of paid time off (PTO) per year. PTO accrues at a rate of 1.67 days per month. Unused PTO may be carried over to the next year, up to a maximum of 5 days. Employees must request PTO at least 2 weeks in advance for periods longer than 3 days.",
    "metadata": {
      "document_type": "hr_policy",
      "filename": "vacation-policy.pdf",
      "allowed_roles": ["employee", "hr", "admin"]
    }
  }'

curl -X POST "${RAG_SERVICE_URL}/index" \
  -H "Content-Type: application/json" \
  -d '{
    "document_id": "hr-remote-work-policy",
    "tenant_id": "'${TENANT_ID}'",
    "content": "Remote Work Policy: Employees may work remotely up to 3 days per week with manager approval. Remote work requests must be submitted through the HR portal. Employees working remotely must maintain core hours of 10am-3pm in their local timezone and be available for video calls during this time.",
    "metadata": {
      "document_type": "hr_policy",
      "filename": "remote-work-policy.pdf",
      "allowed_roles": ["employee", "hr", "admin"]
    }
  }'

echo ""

# =============================================================================
# Sample Technical Documents
# =============================================================================

echo "Loading Technical documents..."

curl -X POST "${RAG_SERVICE_URL}/index" \
  -H "Content-Type: application/json" \
  -d '{
    "document_id": "tech-api-authentication",
    "tenant_id": "'${TENANT_ID}'",
    "content": "API Authentication: All API requests must include a Bearer token in the Authorization header. Tokens are obtained via the /auth/token endpoint using client credentials. Tokens expire after 1 hour and must be refreshed using the refresh_token. Rate limits are 100 requests per minute per client.",
    "metadata": {
      "document_type": "technical",
      "filename": "api-authentication.md",
      "allowed_roles": ["developer", "admin"]
    }
  }'

curl -X POST "${RAG_SERVICE_URL}/index" \
  -H "Content-Type: application/json" \
  -d '{
    "document_id": "tech-database-connection",
    "tenant_id": "'${TENANT_ID}'",
    "content": "Database Connection Guide: The production database uses PostgreSQL 17. Connection strings should use the DATABASE_URL environment variable. Connection pooling is handled by PgBouncer with a default pool size of 20. For debugging connection issues, check the connection pool metrics in Grafana dashboard.",
    "metadata": {
      "document_type": "technical",
      "filename": "database-guide.md",
      "allowed_roles": ["developer", "admin", "devops"]
    }
  }'

echo ""

# =============================================================================
# Sample Contract Documents
# =============================================================================

echo "Loading Contract documents..."

curl -X POST "${RAG_SERVICE_URL}/index" \
  -H "Content-Type: application/json" \
  -d '{
    "document_id": "contract-vendor-agreement",
    "tenant_id": "'${TENANT_ID}'",
    "content": "Vendor Agreement Terms: This agreement is effective for 12 months from the signature date. Either party may terminate with 30 days written notice. The vendor shall maintain liability insurance of at least $1,000,000. All deliverables remain the property of the client upon full payment.",
    "metadata": {
      "document_type": "contracts",
      "filename": "vendor-agreement-template.pdf",
      "allowed_roles": ["legal", "admin", "procurement"]
    }
  }'

curl -X POST "${RAG_SERVICE_URL}/index" \
  -H "Content-Type: application/json" \
  -d '{
    "document_id": "contract-nda-template",
    "tenant_id": "'${TENANT_ID}'",
    "content": "Non-Disclosure Agreement: The receiving party agrees to maintain confidentiality of all proprietary information for a period of 5 years from disclosure. Confidential information includes but is not limited to: technical specifications, business strategies, customer data, and financial information. Breach of this agreement may result in legal action.",
    "metadata": {
      "document_type": "contracts",
      "filename": "nda-template.pdf",
      "allowed_roles": ["legal", "admin", "hr"]
    }
  }'

echo ""
echo "================================================"
echo "Seed data loaded successfully!"
echo "================================================"
echo ""
echo "Test queries:"
echo '  curl -X POST http://localhost:8000/query -H "Content-Type: application/json" -d '"'"'{"question": "What is the vacation policy?", "tenant_id": "demo", "user_roles": ["employee"]}'"'"
echo ""
