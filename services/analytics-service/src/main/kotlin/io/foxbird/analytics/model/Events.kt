package io.foxbird.analytics.model

import jakarta.validation.constraints.NotBlank
import jakarta.validation.constraints.PositiveOrZero

data class QueryEventDto(
    @field:NotBlank val queryId: String,
    @field:NotBlank val tenantId: String,
    @field:NotBlank val userId: String,
    @field:PositiveOrZero val latencyMs: Long,
    @field:NotBlank val modelUsed: String,
    val cacheHit: Boolean,
    @field:PositiveOrZero val sourceCount: Int,
)

data class FeedbackEventDto(
    @field:NotBlank val queryId: String,
    @field:NotBlank val tenantId: String,
    @field:NotBlank val userId: String,
    val liked: Boolean?,
    val comment: String?,
)
