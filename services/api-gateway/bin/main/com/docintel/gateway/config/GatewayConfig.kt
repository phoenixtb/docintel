package com.docintel.gateway.config

import io.netty.channel.ChannelOption
import io.netty.handler.timeout.ReadTimeoutHandler
import io.netty.handler.timeout.WriteTimeoutHandler
import org.springframework.beans.factory.annotation.Value
import org.springframework.cloud.gateway.filter.ratelimit.KeyResolver
import org.springframework.cloud.gateway.filter.ratelimit.RedisRateLimiter
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.context.annotation.Primary
import org.springframework.http.client.reactive.ReactorClientHttpConnector
import org.springframework.web.reactive.function.client.WebClient
import reactor.core.publisher.Mono
import reactor.netty.http.client.HttpClient
import java.time.Duration
import java.util.concurrent.TimeUnit

@Configuration
class GatewayConfig {

    @Bean
    fun opaWebClient(@Value("\${docintel.opa.url:http://opa:8181}") url: String): WebClient {
        val httpClient = HttpClient.create()
            .option(ChannelOption.CONNECT_TIMEOUT_MILLIS, 2_000)
            .responseTimeout(Duration.ofSeconds(3))
            .doOnConnected { conn ->
                conn.addHandlerLast(ReadTimeoutHandler(3, TimeUnit.SECONDS))
                conn.addHandlerLast(WriteTimeoutHandler(3, TimeUnit.SECONDS))
            }
        return WebClient.builder()
            .baseUrl(url)
            .clientConnector(ReactorClientHttpConnector(httpClient))
            .build()
    }

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

