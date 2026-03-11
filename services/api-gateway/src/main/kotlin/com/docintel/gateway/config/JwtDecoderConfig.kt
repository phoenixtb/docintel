package com.docintel.gateway.config

import com.github.benmanes.caffeine.cache.Cache
import com.github.benmanes.caffeine.cache.Caffeine
import org.slf4j.LoggerFactory
import org.springframework.beans.factory.annotation.Value
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import org.springframework.context.annotation.Profile
import org.springframework.security.oauth2.jwt.Jwt
import org.springframework.security.oauth2.jwt.NimbusReactiveJwtDecoder
import org.springframework.security.oauth2.jwt.ReactiveJwtDecoder
import reactor.core.publisher.Mono
import java.security.MessageDigest
import java.time.Duration
import java.time.Instant
import java.util.Base64

/**
 * Caffeine-cached JWT decoder.
 *
 * NimbusReactiveJwtDecoder already caches the JWKS keyset (re-fetches only on unknown kid).
 * But it still parses + verifies the RSA signature on every single request.
 * The same access token is typically reused for the duration of its validity window
 * (oidc-client-ts sends it on every API call), making repeated RSA verifications redundant.
 *
 * This bean wraps Nimbus with a Caffeine cache keyed by the SHA-256 hash of the raw token:
 *  - Cache hit:  return cached Jwt (no crypto)  — sub-millisecond
 *  - Cache miss: delegate to Nimbus (RSA verify) — then cache the result
 *
 * Eviction:
 *  - max 10,000 entries (covers typical concurrent user sessions)
 *  - 5-minute write expiry (short-circuits even within longer-lived tokens)
 *  - exp check on every read: expired tokens are never served from cache
 */
@Configuration
@Profile("!dev")
class JwtDecoderConfig {

    private val log = LoggerFactory.getLogger(javaClass)

    @Bean
    fun jwtDecoder(
        @Value("\${spring.security.oauth2.resourceserver.jwt.jwk-set-uri}") jwkSetUri: String
    ): ReactiveJwtDecoder {
        val delegate = NimbusReactiveJwtDecoder.withJwkSetUri(jwkSetUri).build()

        val cache: Cache<String, Jwt> = Caffeine.newBuilder()
            .maximumSize(10_000)
            .expireAfterWrite(Duration.ofMinutes(5))
            .recordStats()
            .build()

        return ReactiveJwtDecoder { token ->
            val key = token.sha256()
            val cached = cache.getIfPresent(key)

            if (cached != null && cached.expiresAt?.isAfter(Instant.now()) == true) {
                log.trace("JWT cache hit")
                Mono.just(cached)
            } else {
                delegate.decode(token).doOnNext { jwt ->
                    cache.put(key, jwt)
                    log.trace("JWT cache miss — verified and cached")
                }
            }
        }
    }

    private fun String.sha256(): String {
        val digest = MessageDigest.getInstance("SHA-256").digest(toByteArray(Charsets.UTF_8))
        return Base64.getUrlEncoder().withoutPadding().encodeToString(digest)
    }
}
