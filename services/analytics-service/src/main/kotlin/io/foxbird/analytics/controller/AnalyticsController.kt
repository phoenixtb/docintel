package io.foxbird.analytics.controller

import io.foxbird.analytics.model.FeedbackEventDto
import io.foxbird.analytics.model.FeedbackSummaryDto
import io.foxbird.analytics.model.QueryEventDto
import io.foxbird.analytics.model.QuerySummaryDto
import io.foxbird.analytics.service.AnalyticsService
import jakarta.validation.Valid
import kotlinx.coroutines.reactor.mono
import org.springframework.http.HttpStatus
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RequestHeader
import org.springframework.web.bind.annotation.RestController
import reactor.core.publisher.Mono

@RestController
class AnalyticsController(private val service: AnalyticsService) {

    @PostMapping("/events/query")
    fun ingestQuery(@Valid @RequestBody event: QueryEventDto): Mono<ResponseEntity<Void>> = mono {
        service.saveQueryEvent(event)
        ResponseEntity.status(HttpStatus.ACCEPTED).build()
    }

    @PostMapping("/events/feedback")
    fun ingestFeedback(@Valid @RequestBody event: FeedbackEventDto): Mono<ResponseEntity<Void>> = mono {
        service.saveFeedbackEvent(event)
        ResponseEntity.status(HttpStatus.ACCEPTED).build()
    }

    /**
     * Analytics summary endpoints enforce tenant isolation from the gateway header.
     * Platform admin (role=platform_admin) receives global stats (null tenant filter).
     * All other roles are scoped to their tenant.
     */
    @GetMapping("/analytics/queries/summary")
    fun queryStats(
        @RequestHeader(name = "X-Tenant-Id", required = false) tenantId: String?,
        @RequestHeader(name = "X-User-Role", required = false) userRole: String?,
    ): Mono<QuerySummaryDto> = mono {
        val effectiveTenantId = if (userRole == "platform_admin") null else tenantId?.takeIf { it.isNotBlank() }
        service.getQuerySummary(effectiveTenantId)
    }

    @GetMapping("/analytics/feedback/summary")
    fun feedbackStats(
        @RequestHeader(name = "X-Tenant-Id", required = false) tenantId: String?,
        @RequestHeader(name = "X-User-Role", required = false) userRole: String?,
    ): Mono<FeedbackSummaryDto> = mono {
        val effectiveTenantId = if (userRole == "platform_admin") null else tenantId?.takeIf { it.isNotBlank() }
        service.getFeedbackSummary(effectiveTenantId)
    }

    @GetMapping("/health")
    fun health(): Mono<Map<String, String>> = mono { mapOf("status" to "ok", "service" to "analytics-service") }
}
