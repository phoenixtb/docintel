-- Allow ingestion-service (docintel_documents role) to read its own tenant
-- row from admin.tenants — needed by active_model_resolver.resolve_active_vlm_model
-- to read tenant-level llm_vlm_model preferences.
--
-- Pattern mirrors tenant_isolation_tenants for docintel_app: allow when the
-- session var app.current_tenant matches the row id.
DROP POLICY IF EXISTS tenant_isolation_tenants_doc ON admin.tenants;
CREATE POLICY tenant_isolation_tenants_doc ON admin.tenants
    AS PERMISSIVE FOR SELECT TO docintel_documents
    USING (
        current_setting('app.user_role', true) = 'platform_admin'
        OR id = current_setting('app.current_tenant', true)
    );
