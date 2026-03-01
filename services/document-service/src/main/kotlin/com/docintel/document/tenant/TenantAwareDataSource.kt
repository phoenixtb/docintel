package com.docintel.document.tenant

import org.springframework.jdbc.datasource.DelegatingDataSource
import java.sql.Connection
import javax.sql.DataSource

/**
 * DataSource wrapper that enforces PostgreSQL RLS by setting the session variable
 * app.current_tenant at the start of each transaction.
 *
 * Works with PgBouncer in transaction pool mode because SET LOCAL applies only
 * for the duration of the current transaction — the variable is automatically
 * cleared when the transaction ends and the connection is returned to the pool.
 */
class TenantAwareDataSource(delegate: DataSource) : DelegatingDataSource(delegate) {

    override fun getConnection(): Connection = TenantConnection(super.getConnection())

    override fun getConnection(username: String, password: String): Connection =
        TenantConnection(super.getConnection(username, password))
}

/**
 * Connection wrapper that injects SET LOCAL app.current_tenant when a transaction starts
 * (i.e., when setAutoCommit(false) is called by Spring's transaction manager).
 */
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
