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
import org.springframework.web.client.HttpClientErrorException
import org.springframework.web.client.RestTemplate

@Service
class RagServiceClient(
    @Value("\${rag-service.url:http://rag-service:8000}") private val ragServiceUrl: String,
    @Value("\${internal.gateway.secret:}") private val internalSecret: String,
) {
    private val log = LoggerFactory.getLogger(RagServiceClient::class.java)
    private val rest = RestTemplate(SimpleClientHttpRequestFactory().apply {
        setConnectTimeout(5_000)
        setReadTimeout(30_000)
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

    fun deleteAllConversations(tenantId: String) {
        try {
            rest.exchange(
                "$ragServiceUrl/internal/conversations/tenant",
                HttpMethod.DELETE,
                HttpEntity<Void>(headers(tenantId)),
                Void::class.java,
            )
            log.info("Deleted all conversations for tenant {}", tenantId)
        } catch (e: HttpClientErrorException.NotFound) {
            log.debug("No conversations found for tenant {} (404 is OK)", tenantId)
        } catch (e: Exception) {
            log.error("Failed to delete conversations for tenant {}: {}", tenantId, e.message)
            throw e
        }
    }
}
