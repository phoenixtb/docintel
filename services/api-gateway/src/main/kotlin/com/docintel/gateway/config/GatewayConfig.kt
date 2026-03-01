package com.docintel.gateway.config

import org.springframework.beans.factory.annotation.Value
import org.springframework.cloud.gateway.filter.ratelimit.KeyResolver
import org.springframework.cloud.gateway.filter.ratelimit.RedisRateLimiter
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.context.annotation.Primary
import org.springframework.web.reactive.function.client.WebClient
import reactor.core.publisher.Mono

@Configuration
class GatewayConfig {

    @Bean
    fun opaWebClient(@Value("\${docintel.opa.url:http://opa:8181}") url: String): WebClient =
        WebClient.builder().baseUrl(url).build()

    @Bean
    fun tenantRateLimitKeyResolver(): KeyResolver = KeyResolver { exchange ->
        Mono.just(
            exchange.request.headers.getFirst("X-Tenant-Id") ?: "unknown"
        )
    }

    @Bean
    @Primary
    fun defaultRedisRateLimiter(): RedisRateLimiter = RedisRateLimiter(100, 150)

    @Bean
    fun queryRedisRateLimiter(): RedisRateLimiter = RedisRateLimiter(20, 30)
}

