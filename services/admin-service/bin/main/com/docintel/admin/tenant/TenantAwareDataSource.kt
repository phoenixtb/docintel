package com.docintel.admin.tenant

import org.springframework.jdbc.datasource.DelegatingDataSource
import java.sql.Connection
import javax.sql.DataSource

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
