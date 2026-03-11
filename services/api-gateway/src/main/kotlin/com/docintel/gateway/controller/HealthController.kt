package com.docintel.gateway.controller

import org.springframework.beans.factory.annotation.Value
import org.springframework.data.redis.core.ReactiveStringRedisTemplate
import org.springframework.http.HttpStatus
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.RestController
import org.springframework.web.reactive.function.client.WebClient
import reactor.core.publisher.Mono
import java.time.Duration

@RestController
class HealthController(
    private val opaWebClient: WebClient,
    private val redisTemplate: ReactiveStringRedisTemplate,
) {

    @Suppress("UNNECESSARY_SAFE_CALL")
    @GetMapping("/health")
    fun health(): Mono<Map<String, Any>> {
        val opaCheck = opaWebClient.get()
            .uri("/health")
            .retrieve()
            .toBodilessEntity()
            .map<String> { "connected" }
            .timeout(Duration.ofSeconds(2))
            .onErrorReturn("unreachable")

        val redisCheck = redisTemplate.opsForValue()
            .get("__health_probe__")
            .map { "connected" }
            .timeout(Duration.ofSeconds(2))
            .onErrorReturn("unreachable")
            .switchIfEmpty(Mono.just("connected")) // GET on missing key is still healthy

        return Mono.zip(opaCheck, redisCheck) { opa, redis ->
            val status = if (opa == "connected" && redis == "connected") "healthy" else "degraded"
            mapOf(
                "status" to status,
                "service" to "api-gateway",
                "version" to "0.1.0",
                "dependencies" to mapOf(
                    "opa"   to opa,
                    "redis" to redis,
                ),
            )
        }
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
