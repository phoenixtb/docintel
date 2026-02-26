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
import org.springframework.web.bind.annotation.RequestParam
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

    @GetMapping("/analytics/queries/summary")
    fun queryStats(@RequestParam(required = false) tenantId: String?): Mono<QuerySummaryDto> = mono {
        service.getQuerySummary(tenantId)
    }

    @GetMapping("/analytics/feedback/summary")
    fun feedbackStats(@RequestParam(required = false) tenantId: String?): Mono<FeedbackSummaryDto> = mono {
        service.getFeedbackSummary(tenantId)
    }

    @GetMapping("/health")
    fun health(): Mono<Map<String, String>> = mono { mapOf("status" to "ok", "service" to "analytics-service") }
}
