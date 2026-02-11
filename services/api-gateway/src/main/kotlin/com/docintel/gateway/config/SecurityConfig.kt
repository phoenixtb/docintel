package com.docintel.gateway.config

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
     * Development security configuration - allows all requests.
     */
    @Bean
    @Profile("!prod")
    fun securityWebFilterChainDev(http: ServerHttpSecurity): SecurityWebFilterChain {
        return http
            .csrf { it.disable() }
            .cors { it.configurationSource(corsConfigurationSource()) }
            .authorizeExchange { auth ->
                auth
                    .pathMatchers("/actuator/**").permitAll()
                    .pathMatchers("/health").permitAll()
                    .anyExchange().permitAll()
            }
            .build()
    }

    /**
     * Production security configuration - JWT validation via spring-boot-starter-oauth2-resource-server.
     *
     * Auto-configured by Spring via:
     *   spring.security.oauth2.resourceserver.jwt.issuer-uri
     *
     * The starter auto-discovers JWKS endpoint, validates signatures, iss, exp, nbf.
     * No manual JwtDecoder bean needed.
     */
    @Bean
    @Profile("prod")
    fun securityWebFilterChainProd(http: ServerHttpSecurity): SecurityWebFilterChain {
        return http
            .csrf { it.disable() }
            .cors { it.configurationSource(corsConfigurationSource()) }
            .oauth2ResourceServer { oauth2 ->
                oauth2.jwt { }
            }
            .authorizeExchange { auth ->
                auth
                    // Public endpoints
                    .pathMatchers("/actuator/health").permitAll()
                    .pathMatchers("/actuator/info").permitAll()
                    .pathMatchers("/api/v1/health").permitAll()
                    // All other endpoints require authentication
                    .anyExchange().authenticated()
            }
            .build()
    }

    @Bean
    fun corsConfigurationSource(): CorsConfigurationSource {
        val configuration = CorsConfiguration().apply {
            allowedOriginPatterns = listOf("*")
            allowedMethods = listOf("GET", "POST", "PUT", "DELETE", "OPTIONS")
            allowedHeaders = listOf("*")
            exposedHeaders = listOf(
                "X-Tenant-Id",
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
