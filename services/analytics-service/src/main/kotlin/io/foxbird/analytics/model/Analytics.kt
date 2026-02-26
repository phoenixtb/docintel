package io.foxbird.analytics.model

data class QuerySummaryDto(
    val totalQueries: Long,
    val avgLatencyMs: Double,
    val cacheHitRate: Double,
    val p95LatencyMs: Long,
)

data class FeedbackSummaryDto(
    val totalFeedback: Long,
    val likes: Long,
    val dislikes: Long,
    val likeRate: Double,
)
