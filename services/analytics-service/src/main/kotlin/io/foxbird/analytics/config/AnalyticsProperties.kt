package io.foxbird.analytics.config

import org.springframework.boot.context.properties.ConfigurationProperties

@ConfigurationProperties(prefix = "app.analytics")
data class AnalyticsProperties(
    val database: String = "docintel_analytics",
)
