package com.docintel.admin.service

import com.docintel.admin.dto.TenantUser
import com.fasterxml.jackson.annotation.JsonIgnoreProperties
import org.slf4j.LoggerFactory
import org.springframework.beans.factory.annotation.Value
import org.springframework.stereotype.Service
import org.springframework.web.reactive.function.client.WebClient
import org.springframework.web.reactive.function.client.bodyToMono
import java.time.Duration

/**
 * Calls the Zitadel Management REST API for org and user management.
 *
 * Authentication: service account PAT set via ZITADEL_SERVICE_ACCOUNT_PAT env var.
 * If the PAT is not configured, operations are logged as warnings and skipped.
 *
 * Zitadel model mapping:
 *   Authentik group  → Zitadel organization (org name == tenant_id)
 *   Authentik role   → Zitadel project role grant on the user
 */
@Service
class ZitadelService(
    @Value("\${zitadel.url:http://localhost:9090}") private val zitadelUrl: String,
    @Value("\${zitadel.pat:}") private val serviceAccountPat: String,
    @Value("\${zitadel.project-id:}") private val projectId: String
) {
    private val log = LoggerFactory.getLogger(ZitadelService::class.java)

    private val client: WebClient by lazy {
        WebClient.builder()
            .baseUrl(zitadelUrl)
            .defaultHeader("Authorization", "Bearer $serviceAccountPat")
            .defaultHeader("Content-Type", "application/json")
            .codecs { it.defaultCodecs().maxInMemorySize(2 * 1024 * 1024) }
            .build()
    }

    private val timeout = Duration.ofSeconds(10)
    private val configured get() = serviceAccountPat.isNotBlank()

    // ─── Tenant (org) management ──────────────────────────────────────────────

    fun createTenantGroups(tenantId: String) {
        if (!configured) { log.warn("Zitadel PAT not set; skipping org creation for tenant $tenantId"); return }
        try {
            val body = mapOf("name" to tenantId)
            val resp = client.post().uri("/v2/organizations")
                .bodyValue(body)
                .retrieve()
                .bodyToMono<Map<*, *>>()
                .timeout(timeout)
                .block()

            val orgId = resp?.get("organizationId") as? String
            if (orgId == null) {
                log.warn("Zitadel did not return org ID for tenant $tenantId (may already exist)")
                return
            }

            // Grant the DocIntel project to the new org
            if (projectId.isNotBlank()) {
                grantProjectToOrg(orgId, listOf("tenant_admin", "tenant_user"))
            }

            log.info("Created Zitadel org '{}' for tenant {}", tenantId, tenantId)
        } catch (e: Exception) {
            log.error("Failed to create Zitadel org for tenant $tenantId: ${e.message}")
        }
    }

    fun deleteTenantGroup(tenantId: String) {
        if (!configured) { log.warn("Zitadel PAT not set; skipping org deletion for tenant $tenantId"); return }
        val orgId = findOrgByName(tenantId) ?: run {
            log.warn("Zitadel org not found for tenant $tenantId; skipping deletion")
            return
        }
        try {
            client.post().uri("/v2/organizations/$orgId/deactivate")
                .retrieve()
                .bodyToMono<Map<*, *>>()
                .timeout(timeout)
                .block()
            log.info("Deactivated Zitadel org for tenant $tenantId")
        } catch (e: Exception) {
            log.error("Failed to deactivate Zitadel org for tenant $tenantId: ${e.message}")
        }
    }

    fun getUsersByTenant(tenantId: String): List<TenantUser> {
        if (!configured) {
            log.warn("Zitadel PAT not set; returning empty user list for tenant $tenantId")
            return emptyList()
        }
        val orgId = findOrgByName(tenantId) ?: return emptyList()
        return try {
            val resp = client.post()
                .uri("/management/v1/users/_search")
                .header("x-zitadel-orgid", orgId)
                .bodyValue(mapOf("limit" to 250))
                .retrieve()
                .bodyToMono<ZitadelUserListResponse>()
                .timeout(timeout)
                .block()

            resp?.result?.mapNotNull { user ->
                val role = getUserRole(user.id, orgId)
                TenantUser(
                    id = user.id,
                    email = user.human?.email?.email ?: "",
                    username = user.userName,
                    name = "${user.human?.profile?.firstName ?: ""} ${user.human?.profile?.lastName ?: ""}".trim(),
                    role = role,
                    tenantId = tenantId
                )
            } ?: emptyList()
        } catch (e: Exception) {
            log.error("Failed to fetch Zitadel users for tenant $tenantId: ${e.message}")
            emptyList()
        }
    }

    fun updateUserRole(userId: String, tenantId: String, role: String) {
        if (!configured) { log.warn("Zitadel PAT not set; skipping role update for user $userId"); return }
        val orgId = findOrgByName(tenantId) ?: run {
            log.warn("Zitadel org not found for tenant $tenantId; skipping role update")
            return
        }
        try {
            // Find existing grant for this project in this org
            val grants = client.get()
                .uri("/management/v1/users/$userId/grants?queries[0].projectIdQuery.projectId=$projectId")
                .header("x-zitadel-orgid", orgId)
                .retrieve()
                .bodyToMono<ZitadelGrantListResponse>()
                .timeout(timeout)
                .block()

            val existing = grants?.result?.firstOrNull()
            if (existing != null) {
                client.put().uri("/management/v1/users/$userId/grants/${existing.id}")
                    .header("x-zitadel-orgid", orgId)
                    .bodyValue(mapOf("roleKeys" to listOf(role)))
                    .retrieve()
                    .bodyToMono<Map<*, *>>()
                    .timeout(timeout)
                    .block()
            } else {
                client.post().uri("/management/v1/users/$userId/grants")
                    .header("x-zitadel-orgid", orgId)
                    .bodyValue(mapOf("projectId" to projectId, "roleKeys" to listOf(role)))
                    .retrieve()
                    .bodyToMono<Map<*, *>>()
                    .timeout(timeout)
                    .block()
            }
            log.info("Updated role for user $userId in tenant $tenantId to $role")
        } catch (e: Exception) {
            log.error("Failed to update role for user $userId: ${e.message}")
        }
    }

    // ─── Private helpers ──────────────────────────────────────────────────────

    private fun findOrgByName(name: String): String? {
        return try {
            val resp = client.post().uri("/v2/organizations/_search")
                .bodyValue(mapOf("queries" to listOf(mapOf("nameQuery" to mapOf("name" to name, "method" to "TEXT_QUERY_METHOD_EQUALS")))))
                .retrieve()
                .bodyToMono<ZitadelOrgListResponse>()
                .timeout(timeout)
                .block()
            resp?.result?.firstOrNull()?.id
        } catch (e: Exception) {
            log.error("Failed to search for Zitadel org '$name': ${e.message}")
            null
        }
    }

    private fun grantProjectToOrg(orgId: String, roles: List<String>) {
        try {
            client.post().uri("/management/v1/projects/$projectId/grants")
                .bodyValue(mapOf("grantedOrgId" to orgId, "roleKeys" to roles))
                .retrieve()
                .bodyToMono<Map<*, *>>()
                .timeout(timeout)
                .block()
        } catch (e: Exception) {
            log.warn("Failed to grant project to org $orgId: ${e.message}")
        }
    }

    private fun getUserRole(userId: String, orgId: String): String {
        return try {
            val resp = client.get()
                .uri("/management/v1/users/$userId/grants?queries[0].projectIdQuery.projectId=$projectId")
                .header("x-zitadel-orgid", orgId)
                .retrieve()
                .bodyToMono<ZitadelGrantListResponse>()
                .timeout(timeout)
                .block()
            val roles = resp?.result?.firstOrNull()?.roleKeys ?: emptyList()
            when {
                "platform_admin" in roles -> "platform_admin"
                "tenant_admin" in roles -> "tenant_admin"
                else -> "tenant_user"
            }
        } catch (e: Exception) {
            "tenant_user"
        }
    }

    // ─── Zitadel API response models ──────────────────────────────────────────

    @JsonIgnoreProperties(ignoreUnknown = true)
    data class ZitadelOrgListResponse(val result: List<ZitadelOrg> = emptyList())

    @JsonIgnoreProperties(ignoreUnknown = true)
    data class ZitadelOrg(val id: String = "", val name: String = "")

    @JsonIgnoreProperties(ignoreUnknown = true)
    data class ZitadelUserListResponse(val result: List<ZitadelUser> = emptyList())

    @JsonIgnoreProperties(ignoreUnknown = true)
    data class ZitadelUser(
        val id: String = "",
        val userName: String = "",
        val human: ZitadelHuman? = null
    )

    @JsonIgnoreProperties(ignoreUnknown = true)
    data class ZitadelHuman(
        val profile: ZitadelProfile? = null,
        val email: ZitadelEmail? = null
    )

    @JsonIgnoreProperties(ignoreUnknown = true)
    data class ZitadelProfile(val firstName: String = "", val lastName: String = "")

    @JsonIgnoreProperties(ignoreUnknown = true)
    data class ZitadelEmail(val email: String = "")

    @JsonIgnoreProperties(ignoreUnknown = true)
    data class ZitadelGrantListResponse(val result: List<ZitadelGrant> = emptyList())

    @JsonIgnoreProperties(ignoreUnknown = true)
    data class ZitadelGrant(val id: String = "", val roleKeys: List<String> = emptyList())
}
