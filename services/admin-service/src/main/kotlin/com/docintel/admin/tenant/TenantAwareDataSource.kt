package com.docintel.admin.tenant

import org.springframework.jdbc.datasource.DelegatingDataSource
import java.sql.Connection
import javax.sql.DataSource

class TenantAwareDataSource(delegate: DataSource) : DelegatingDataSource(delegate) {

    override fun getConnection(): Connection = TenantConnection(super.getConnection())

    override fun getConnection(username: String, password: String): Connection =
        TenantConnection(super.getConnection(username, password))
}

class TenantConnection(private val delegate: Connection) : Connection by delegate {

    override fun setAutoCommit(autoCommit: Boolean) {
        delegate.setAutoCommit(autoCommit)
        if (!autoCommit) {
            val tenant = TenantContextHolder.getTenantId().replace("'", "''")
            val role   = TenantContextHolder.getUserRole().replace("'", "''")
            delegate.createStatement().use { stmt ->
                stmt.execute("SET LOCAL app.current_tenant = '$tenant'")
                stmt.execute("SET LOCAL app.current_role = '$role'")
            }
        }
    }
}
