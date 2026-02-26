package io.foxbird.analytics.config

import org.slf4j.LoggerFactory
import org.springframework.boot.ApplicationArguments
import org.springframework.boot.ApplicationRunner
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Component

@Component
class SchemaInitializer(
    private val jdbc: JdbcTemplate,
    private val props: AnalyticsProperties,
) : ApplicationRunner {

    private val log = LoggerFactory.getLogger(javaClass)
    private val db get() = props.database

    override fun run(args: ApplicationArguments) {
        log.info("Initializing ClickHouse schema in database '{}'", db)

        jdbc.execute("CREATE DATABASE IF NOT EXISTS `$db`")

        jdbc.execute(
            """
            CREATE TABLE IF NOT EXISTS `$db`.query_events (
                query_id      String,
                tenant_id     String,
                user_id       String,
                latency_ms    UInt32,
                model_used    LowCardinality(String),
                cache_hit     Bool,
                source_count  UInt8,
                created_at    DateTime DEFAULT now()
            ) ENGINE = MergeTree()
            ORDER BY (tenant_id, created_at)
            """.trimIndent()
        )

        jdbc.execute(
            """
            CREATE TABLE IF NOT EXISTS `$db`.feedback_events (
                query_id    String,
                tenant_id   String,
                user_id     String,
                liked       Nullable(Bool),
                comment     Nullable(String),
                created_at  DateTime DEFAULT now()
            ) ENGINE = MergeTree()
            ORDER BY (tenant_id, created_at)
            """.trimIndent()
        )

        log.info("ClickHouse schema ready")
    }
}
