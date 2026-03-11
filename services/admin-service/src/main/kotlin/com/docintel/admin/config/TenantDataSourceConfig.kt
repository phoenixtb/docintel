package com.docintel.admin.config

import com.docintel.admin.tenant.TenantAwareDataSource
import com.zaxxer.hikari.HikariConfig
import com.zaxxer.hikari.HikariDataSource
import org.flywaydb.core.Flyway
import org.springframework.beans.factory.annotation.Value
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.context.annotation.Primary
import javax.sql.DataSource

@Configuration
class TenantDataSourceConfig(
    @Value("\${spring.datasource.url}") private val url: String,
    @Value("\${spring.datasource.username}") private val username: String,
    @Value("\${spring.datasource.password}") private val password: String,
    // DDL user has schema-level CREATE privilege needed for Flyway history table.
    // Falls back to the runtime user if not set (local dev without RLS).
    @Value("\${flyway.ddl.username:\${spring.datasource.username}}") private val flywayUsername: String,
    @Value("\${flyway.ddl.password:\${spring.datasource.password}}") private val flywayPassword: String,
) {

    @Bean("hikariDataSource")
    fun hikariDataSource(): HikariDataSource =
        HikariDataSource(HikariConfig().apply {
            jdbcUrl = url
            this.username = username
            this.password = password
            maximumPoolSize = 10
            minimumIdle = 2
            connectionTimeout = 30_000
            idleTimeout = 600_000
            maxLifetime = 1_800_000
        })

    @Bean
    @Primary
    fun dataSource(hikariDataSource: HikariDataSource): DataSource =
        TenantAwareDataSource(hikariDataSource)

    // Run Flyway migrations with the DDL user (schema owner) rather than the runtime
    // app user, which has no CREATE privilege on the public schema. Also bypasses
    // TenantAwareDataSource to avoid RLS interference during DDL.
    //
    // The JDBC URL may embed user/password as query params (e.g. ?user=docintel_app).
    // Those params take priority over HikariCP's username/password setters in the
    // PostgreSQL driver, so we strip them and pass credentials separately.
    @Bean
    fun flyway(): Flyway {
        val baseUrl = url
            .replace(Regex("[?&]user=[^&]*"), "")
            .replace(Regex("[?&]password=[^&]*"), "")
            .replace(Regex("\\?$"), "")  // clean trailing '?' if all params stripped

        val flywayDs = HikariDataSource(HikariConfig().apply {
            jdbcUrl = baseUrl
            this.username = flywayUsername
            this.password = flywayPassword
            maximumPoolSize = 2
            connectionTimeout = 30_000
        })
        return Flyway.configure()
            .dataSource(flywayDs)
            .locations("classpath:db/migration")
            .baselineOnMigrate(true)
            .baselineVersion("0")
            .load()
            .also { it.migrate() }
    }
}
