package com.docintel.gateway.config

import org.springframework.beans.factory.annotation.Value
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.context.annotation.Profile
import org.springframework.security.config.annotation.web.reactive.EnableWebFluxSecurity
import org.springframework.security.config.web.server.ServerHttpSecurity
import org.springframework.security.web.server.SecurityWebFilterChain
import org.springframework.web.cors.CorsConfiguration
import org.springframework.web.cors.reactive.CorsConfigurationSource
import org.springframework.web.cors.reactive.UrlBasedCorsConfigurationSource

@Configuration
@EnableWebFluxSecurity
class SecurityConfig {

    /**
     * Default security configuration — JWT validation active for ALL profiles including docker.
     * Requires spring.security.oauth2.resourceserver.jwt.issuer-uri to be set.
     * Apply to any profile other than "dev" (local IDE runs without Authentik).
     */
    @Bean
    @Profile("!dev")
    fun securityWebFilterChain(
        http: ServerHttpSecurity,
        corsSource: CorsConfigurationSource,
    ): SecurityWebFilterChain {
        return http
            .csrf { it.disable() }
            .cors { it.configurationSource(corsSource) }
            .oauth2ResourceServer { oauth2 ->
                oauth2.jwt { }
            }
            .authorizeExchange { auth ->
                auth
                    .pathMatchers("/actuator/health").permitAll()
                    .pathMatchers("/actuator/info").permitAll()
                    .pathMatchers("/api/v1/health").permitAll()
                    .anyExchange().authenticated()
            }
            .build()
    }

    /**
     * Dev-only security — permitAll for local IDE runs without Authentik.
     */
    @Bean
    @Profile("dev")
    fun securityWebFilterChainDev(
        http: ServerHttpSecurity,
        corsSource: CorsConfigurationSource,
    ): SecurityWebFilterChain {
        return http
            .csrf { it.disable() }
            .cors { it.configurationSource(corsSource) }
            .authorizeExchange { auth ->
                auth.anyExchange().permitAll()
            }
            .build()
    }

    @Bean
    fun corsConfigurationSource(
        @Value("\${docintel.cors.allowed-origins:http://localhost:3001}") allowedOriginsRaw: String
    ): CorsConfigurationSource {
        val origins = allowedOriginsRaw.split(",").map { it.trim() }.filter { it.isNotEmpty() }
        val configuration = CorsConfiguration().apply {
            allowedOrigins = origins
            allowedMethods = listOf("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
            allowedHeaders = listOf("*")
            exposedHeaders = listOf(
                "X-Tenant-Id",
                "X-Request-Id",
                "X-RateLimit-Limit",
                "X-RateLimit-Remaining",
                "Retry-After"
            )
            allowCredentials = true
            maxAge = 3600L
        }

        return UrlBasedCorsConfigurationSource().apply {
            registerCorsConfiguration("/**", configuration)
        }
    }
}
