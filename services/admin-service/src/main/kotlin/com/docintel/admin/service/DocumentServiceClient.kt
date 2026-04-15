package com.docintel.admin.service

import com.docintel.admin.filter.HmacUtils
import org.slf4j.LoggerFactory
import org.slf4j.MDC
import org.springframework.beans.factory.annotation.Value
import org.springframework.http.HttpEntity
import org.springframework.http.HttpHeaders
import org.springframework.http.HttpMethod
import org.springframework.http.client.SimpleClientHttpRequestFactory
import org.springframework.stereotype.Service
import org.springframework.web.client.RestTemplate

/**
 * Internal HTTP client for document-service.
 *
 * Authenticates with HMAC-SHA256 service token (empty requestId/userId).
 * Forwards X-Request-Id for end-to-end trace propagation.
 */
@Service
class DocumentServiceClient(
    @Value("\${document-service.url:http://document-service:8081}") private val documentServiceUrl: String,
    @Value("\${internal.gateway.secret:}") private val internalSecret: String,
) {
    private val log = LoggerFactory.getLogger(DocumentServiceClient::class.java)
    private val rest = RestTemplate(SimpleClientHttpRequestFactory().apply {
        setConnectTimeout(5_000)
        setReadTimeout(60_000)
    })

    private fun headers(tenantId: String): HttpHeaders {
        if (internalSecret.isBlank()) {
            error("INTERNAL_GATEWAY_SECRET not configured — refusing unauthenticated internal call (fail-secure)")
        }
        val token = HmacUtils.compute(":$tenantId:", internalSecret)
        return HttpHeaders().apply {
            set("X-Tenant-Id", tenantId)
            set("X-Internal-Service-Token", token)
            MDC.get("requestId")?.let { set("X-Request-Id", it) }
        }
    }

    fun deleteAllDocuments(tenantId: String) {
        try {
            rest.exchange(
                "$documentServiceUrl/internal/documents/all",
                HttpMethod.DELETE,
                HttpEntity<Void>(headers(tenantId)),
                Void::class.java,
            )
            log.info("Deleted all documents for tenant {}", tenantId)
        } catch (e: Exception) {
            log.error("Failed to delete documents for tenant {}: {}", tenantId, e.message)
            throw e
        }
    }
}
