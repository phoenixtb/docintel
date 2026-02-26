package io.foxbird.analytics.repository

import io.foxbird.analytics.config.AnalyticsProperties
import io.foxbird.analytics.model.FeedbackEventDto
import io.foxbird.analytics.model.FeedbackSummaryDto
import io.foxbird.analytics.model.QueryEventDto
import io.foxbird.analytics.model.QuerySummaryDto
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.jdbc.core.RowMapper
import org.springframework.stereotype.Repository

@Repository
class AnalyticsRepository(
    private val jdbc: JdbcTemplate,
    private val props: AnalyticsProperties,
) {
    private val db get() = props.database

    fun insertQueryEvent(e: QueryEventDto) {
        jdbc.update(
            """INSERT INTO `$db`.query_events
               (query_id, tenant_id, user_id, latency_ms, model_used, cache_hit, source_count)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            e.queryId, e.tenantId, e.userId, e.latencyMs, e.modelUsed, e.cacheHit, e.sourceCount,
        )
    }

    fun insertFeedbackEvent(e: FeedbackEventDto) {
        jdbc.update(
            """INSERT INTO `$db`.feedback_events
               (query_id, tenant_id, user_id, liked, comment)
               VALUES (?, ?, ?, ?, ?)""",
            e.queryId, e.tenantId, e.userId, e.liked, e.comment,
        )
    }

    fun queryEventsSummary(tenantId: String?): QuerySummaryDto {
        val baseSql =
            """SELECT
                 count()                          AS total,
                 avg(latency_ms)                  AS avg_lat,
                 countIf(cache_hit) / count()     AS cache_rate,
                 quantile(0.95)(latency_ms)       AS p95
               FROM `$db`.query_events"""

        val mapper = RowMapper { rs, _ ->
            QuerySummaryDto(
                totalQueries = rs.getLong("total"),
                avgLatencyMs = rs.getDouble("avg_lat"),
                cacheHitRate = rs.getDouble("cache_rate"),
                p95LatencyMs = rs.getLong("p95"),
            )
        }

        return if (tenantId != null)
            jdbc.queryForObject("$baseSql WHERE tenant_id = ?", mapper, tenantId)!!
        else
            jdbc.queryForObject(baseSql, mapper)!!
    }

    fun feedbackSummary(tenantId: String?): FeedbackSummaryDto {
        val baseSql =
            """SELECT
                 count()             AS total,
                 countIf(liked = 1)  AS likes,
                 countIf(liked = 0)  AS dislikes
               FROM `$db`.feedback_events"""

        val mapper = RowMapper { rs, _ ->
            val total = rs.getLong("total")
            val likes = rs.getLong("likes")
            FeedbackSummaryDto(
                totalFeedback = total,
                likes = likes,
                dislikes = rs.getLong("dislikes"),
                likeRate = if (total > 0) likes.toDouble() / total else 0.0,
            )
        }

        return if (tenantId != null)
            jdbc.queryForObject("$baseSql WHERE tenant_id = ?", mapper, tenantId)!!
        else
            jdbc.queryForObject(baseSql, mapper)!!
    }
}
