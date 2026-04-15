package com.docintel.document.config

import com.docintel.document.tenant.TenantAwareDataSource
import com.zaxxer.hikari.HikariConfig
import com.zaxxer.hikari.HikariDataSource
import org.flywaydb.core.Flyway
import org.springframework.beans.factory.annotation.Value
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.context.annotation.Primary
import javax.sql.DataSource

/**
 * Provides TenantAwareDataSource wrapping HikariCP.
 * Flyway runs as the DDL user (docintel superuser) targeting the documents schema,
 * keeping schema history isolated from admin-service's admin.flyway_schema_history.
 */
@Configuration(proxyBeanMethods = false)
class TenantDataSourceConfig(
    @Value("\${spring.datasource.url}") private val url: String,
    @Value("\${spring.datasource.username}") private val username: String,
    @Value("\${spring.datasource.password}") private val password: String,
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

    @Bean
    fun flyway(): Flyway {
        val baseUrl = url
            .replace(Regex("[?&]user=[^&]*"), "")
            .replace(Regex("[?&]password=[^&]*"), "")
            .replace(Regex("\\?$"), "")

        val flywayDs = HikariDataSource(HikariConfig().apply {
            jdbcUrl = baseUrl
            this.username = flywayUsername
            this.password = flywayPassword
            maximumPoolSize = 2
            connectionTimeout = 30_000
            connectionInitSql = "SET search_path = documents, admin, public"
        })
        return Flyway.configure()
            .dataSource(flywayDs)
            .schemas("documents")
            .defaultSchema("documents")
            .locations("classpath:db/migration")
            .baselineOnMigrate(true)
            .baselineVersion("0")
            .load()
            .also { it.migrate() }
    }
}
