package com.docintel.document.tenant

import org.springframework.jdbc.datasource.DelegatingDataSource
import java.sql.Connection
import javax.sql.DataSource

/**
 * DataSource wrapper that enforces PostgreSQL RLS by setting tenant session variables
 * at connection acquisition time (not at transaction start).
 *
 * Why SET (session-level) at getConnection() instead of SET LOCAL in setAutoCommit():
 *   - Hibernate 6.x uses lazy connection acquisition — by the time setAutoCommit(false)
 *     fires the JDBC transaction may already be past the point where SET LOCAL would bind.
 *   - HikariCP always calls our getConnection() wrapper before handing out a connection,
 *     so the variable is always refreshed for the current request/tenant context.
 *   - SET (session-level) is safe because every connection checkout overwrites the value
 *     with the correct tenant from TenantContextHolder.
 */
class TenantAwareDataSource(delegate: DataSource) : DelegatingDataSource(delegate) {

    override fun getConnection(): Connection =
        super.getConnection().also { applyTenantContext(it) }

    override fun getConnection(username: String, password: String): Connection =
        super.getConnection(username, password).also { applyTenantContext(it) }

    private fun applyTenantContext(conn: Connection) {
        // Use PreparedStatement to safely bind session variables —
        // avoids injection via special characters in tenant/role values.
        conn.prepareStatement("SELECT set_config('app.current_tenant', ?, false)").use { it.setString(1, TenantContextHolder.getTenantId()); it.execute() }
        conn.prepareStatement("SELECT set_config('app.user_role', ?, false)").use { it.setString(1, TenantContextHolder.getUserRole()); it.execute() }
    }
}
