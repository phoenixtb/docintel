package com.docintel.admin.service

import com.docintel.admin.dto.TenantUser
import com.fasterxml.jackson.annotation.JsonIgnoreProperties
import com.fasterxml.jackson.annotation.JsonProperty
import org.slf4j.LoggerFactory
import org.springframework.beans.factory.annotation.Value
import org.springframework.stereotype.Service
import org.springframework.web.reactive.function.client.WebClient
import org.springframework.web.reactive.function.client.bodyToMono

/**
 * Calls the Authentik REST API (v3) for group and user management.
 *
 * Requires a service account API token set via AUTHENTIK_TOKEN env var.
 * If the token is not configured, operations are skipped with a warning.
 */
@Service
class AuthentikService(
    @Value("\${authentik.url:http://localhost:9090}") private val authentikUrl: String,
    @Value("\${authentik.token:}") private val authentikToken: String
) {
    private val log = LoggerFactory.getLogger(AuthentikService::class.java)

    private val client: WebClient by lazy {
        WebClient.builder()
            .baseUrl(authentikUrl)
            .defaultHeader("Authorization", "Bearer $authentikToken")
            .defaultHeader("Content-Type", "application/json")
            .build()
    }

    private val configured get() = authentikToken.isNotBlank()

    // ---- Groups ----

    fun createTenantGroup(tenantId: String, role: String = "tenant_user") {
        if (!configured) { log.warn("Authentik token not set; skipping group creation for tenant $tenantId"); return }
        val body = mapOf(
            "name" to "tenant-$tenantId",
            "attributes" to mapOf("tenant_id" to tenantId, "role" to role)
        )
        try {
            client.post().uri("/api/v3/core/groups/")
                .bodyValue(body)
                .retrieve()
                .bodyToMono<Map<*, *>>()
                .block()
            log.info("Created Authentik group for tenant $tenantId")
        } catch (e: Exception) {
            log.error("Failed to create Authentik group for tenant $tenantId: ${e.message}")
        }
    }

    fun deleteTenantGroup(tenantId: String) {
        if (!configured) { log.warn("Authentik token not set; skipping group deletion for tenant $tenantId"); return }
        val groupPk = findGroupPk("tenant-$tenantId") ?: return
        try {
            client.delete().uri("/api/v3/core/groups/$groupPk/")
                .retrieve()
                .bodyToMono<Void>()
                .block()
            log.info("Deleted Authentik group for tenant $tenantId")
        } catch (e: Exception) {
            log.error("Failed to delete Authentik group for tenant $tenantId: ${e.message}")
        }
    }

    fun getUsersByTenant(tenantId: String): List<TenantUser> {
        if (!configured) {
            log.warn("Authentik token not set; returning empty user list for tenant $tenantId")
            return emptyList()
        }
        return try {
            val response = client.get()
                .uri("/api/v3/core/users/?groups__attributes_has_key=tenant_id")
                .retrieve()
                .bodyToMono<AuthentikUserListResponse>()
                .block()

            response?.results
                ?.filter { user ->
                    user.groups.any { g -> g.attributes["tenant_id"] == tenantId }
                }
                ?.map { user ->
                    val group = user.groups.firstOrNull { g -> g.attributes["tenant_id"] == tenantId }
                    val role = group?.attributes?.get("role") as? String ?: "tenant_user"
                    TenantUser(
                        id = user.pk.toString(),
                        email = user.email,
                        username = user.username,
                        name = user.name,
                        role = role,
                        tenantId = tenantId
                    )
                } ?: emptyList()
        } catch (e: Exception) {
            log.error("Failed to fetch Authentik users for tenant $tenantId: ${e.message}")
            emptyList()
        }
    }

    fun updateUserRole(userId: String, tenantId: String, role: String) {
        if (!configured) { log.warn("Authentik token not set; skipping role update for user $userId"); return }
        val groupName = if (role == "tenant_admin") "tenant-$tenantId-admin" else "tenant-$tenantId"
        val groupPk = findGroupPk(groupName)
        if (groupPk == null) {
            log.warn("Group $groupName not found in Authentik; skipping role update")
            return
        }
        try {
            // Fetch current user groups, remove old tenant groups, add new
            val user = client.get().uri("/api/v3/core/users/$userId/")
                .retrieve()
                .bodyToMono<AuthentikUser>()
                .block() ?: return

            val otherGroupPks = user.groups.filter { g ->
                g.attributes["tenant_id"] != tenantId
            }.map { it.pk }

            val newGroupPks = otherGroupPks + groupPk

            client.patch().uri("/api/v3/core/users/$userId/")
                .bodyValue(mapOf("groups" to newGroupPks))
                .retrieve()
                .bodyToMono<Map<*, *>>()
                .block()

            log.info("Updated role for user $userId in tenant $tenantId to $role")
        } catch (e: Exception) {
            log.error("Failed to update role for user $userId: ${e.message}")
        }
    }

    private fun findGroupPk(name: String): Int? {
        return try {
            val response = client.get()
                .uri("/api/v3/core/groups/?name=$name")
                .retrieve()
                .bodyToMono<AuthentikGroupListResponse>()
                .block()
            response?.results?.firstOrNull()?.pk
        } catch (e: Exception) {
            log.error("Failed to find group $name in Authentik: ${e.message}")
            null
        }
    }

    // ---- Authentik API response models ----

    @JsonIgnoreProperties(ignoreUnknown = true)
    data class AuthentikUserListResponse(val results: List<AuthentikUser> = emptyList())

    @JsonIgnoreProperties(ignoreUnknown = true)
    data class AuthentikUser(
        val pk: Int = 0,
        val username: String = "",
        val name: String = "",
        val email: String = "",
        val groups: List<AuthentikGroup> = emptyList()
    )

    @JsonIgnoreProperties(ignoreUnknown = true)
    data class AuthentikGroupListResponse(val results: List<AuthentikGroupRef> = emptyList())

    @JsonIgnoreProperties(ignoreUnknown = true)
    data class AuthentikGroupRef(val pk: Int = 0, val name: String = "")

    @JsonIgnoreProperties(ignoreUnknown = true)
    data class AuthentikGroup(
        val pk: Int = 0,
        val name: String = "",
        val attributes: Map<String, Any> = emptyMap()
    )
}
