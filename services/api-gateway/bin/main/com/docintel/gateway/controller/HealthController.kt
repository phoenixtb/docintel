package com.docintel.gateway.controller

import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RestController

@RestController
class HealthController {

    @GetMapping("/health")
    fun health(): Map<String, String> {
        return mapOf(
            "status" to "healthy",
            "service" to "api-gateway",
            "version" to "0.1.0"
        )
    }

    @GetMapping("/")
    fun root(): Map<String, Any> {
        return mapOf(
            "service" to "DocIntel API Gateway",
            "version" to "0.1.0",
            "endpoints" to mapOf(
                "documents" to "/api/v1/documents",
                "query" to "/api/v1/query",
                "query_stream" to "/api/v1/query/stream",
                "admin" to "/api/v1/admin",
                "health" to "/health"
            )
        )
    }
}
