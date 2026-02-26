package io.foxbird.analytics.service

import io.foxbird.analytics.model.FeedbackEventDto
import io.foxbird.analytics.model.FeedbackSummaryDto
import io.foxbird.analytics.model.QueryEventDto
import io.foxbird.analytics.model.QuerySummaryDto
import io.foxbird.analytics.repository.AnalyticsRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.springframework.stereotype.Service

@Service
class AnalyticsService(private val repo: AnalyticsRepository) {

    suspend fun saveQueryEvent(event: QueryEventDto) =
        withContext(Dispatchers.IO) { repo.insertQueryEvent(event) }

    suspend fun saveFeedbackEvent(event: FeedbackEventDto) =
        withContext(Dispatchers.IO) { repo.insertFeedbackEvent(event) }

    suspend fun getQuerySummary(tenantId: String?): QuerySummaryDto =
        withContext(Dispatchers.IO) { repo.queryEventsSummary(tenantId) }

    suspend fun getFeedbackSummary(tenantId: String?): FeedbackSummaryDto =
        withContext(Dispatchers.IO) { repo.feedbackSummary(tenantId) }
}
