package com.docintel.document.config

import com.docintel.document.tenant.TenantAwareDataSource
import com.zaxxer.hikari.HikariConfig
import com.zaxxer.hikari.HikariDataSource
import org.springframework.beans.factory.annotation.Value
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.context.annotation.Primary
import javax.sql.DataSource

/**
 * Provides TenantAwareDataSource wrapping HikariCP.
 * Spring Boot DataSource auto-configuration is suppressed by this @Primary bean.
 */
@Configuration
class TenantDataSourceConfig(
    @Value("\${spring.datasource.url}") private val url: String,
    @Value("\${spring.datasource.username}") private val username: String,
    @Value("\${spring.datasource.password}") private val password: String,
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
}
