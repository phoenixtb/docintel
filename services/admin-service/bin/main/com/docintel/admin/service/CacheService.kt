package com.docintel.admin.service

import com.docintel.admin.dto.CacheStats
import com.docintel.admin.dto.ClearCacheResponse
import org.springframework.beans.factory.annotation.Value
import org.springframework.stereotype.Service
import org.springframework.web.client.RestTemplate
import org.springframework.http.HttpEntity
import org.springframework.http.HttpHeaders
import org.springframework.http.MediaType
import java.time.Instant

/**
 * Cache service using Qdrant HTTP API for simplicity.
 */
@Service
class CacheService(
    @Value("\${qdrant.url:http://localhost:6333}")
    private val qdrantUrl: String
) {
    private val restTemplate = RestTemplate()
    private val cacheCollection = "response_cache"

    fun getCacheStats(): CacheStats {
        return try {
            val response = restTemplate.getForObject(
                "$qdrantUrl/collections/$cacheCollection",
                Map::class.java
            )
            
            @Suppress("UNCHECKED_CAST")
            val result = response?.get("result") as? Map<String, Any>
            val pointsCount = (result?.get("points_count") as? Number)?.toLong() ?: 0L
            
            CacheStats(
                totalEntries = pointsCount,
                hitRate = 0.0,
                avgLatencySavedMs = 0,
                oldestEntry = null,
                newestEntry = null
            )
        } catch (e: Exception) {
            CacheStats(
                totalEntries = 0,
                hitRate = 0.0,
                avgLatencySavedMs = 0,
                oldestEntry = null,
                newestEntry = null
            )
        }
    }

    fun clearAllCache(): ClearCacheResponse {
        return try {
            // Get current count
            val stats = getCacheStats()
            val count = stats.totalEntries

            // Delete collection
            try {
                restTemplate.delete("$qdrantUrl/collections/$cacheCollection")
            } catch (e: Exception) {
                // Collection might not exist
            }

            // Recreate collection
            val headers = HttpHeaders()
            headers.contentType = MediaType.APPLICATION_JSON
            
            val body = """
                {
                    "vectors": {
                        "size": 768,
                        "distance": "Cosine"
                    }
                }
            """.trimIndent()

            restTemplate.put(
                "$qdrantUrl/collections/$cacheCollection",
                HttpEntity(body, headers)
            )

            // Recreate tenant_id index
            val indexBody = """{"field_name": "tenant_id", "field_schema": "keyword"}"""
            restTemplate.put(
                "$qdrantUrl/collections/$cacheCollection/index",
                HttpEntity(indexBody, headers)
            )

            ClearCacheResponse(
                success = true,
                entriesCleared = count,
                tenantId = null
            )
        } catch (e: Exception) {
            ClearCacheResponse(
                success = false,
                entriesCleared = 0,
                tenantId = null
            )
        }
    }

    fun clearTenantCache(tenantId: String): ClearCacheResponse {
        return try {
            val headers = HttpHeaders()
            headers.contentType = MediaType.APPLICATION_JSON

            // Delete points matching tenant_id using scroll and delete
            val filterBody = """
                {
                    "filter": {
                        "must": [
                            {
                                "key": "tenant_id",
                                "match": {"value": "$tenantId"}
                            }
                        ]
                    }
                }
            """.trimIndent()

            restTemplate.postForObject(
                "$qdrantUrl/collections/$cacheCollection/points/delete",
                HttpEntity(filterBody, headers),
                Map::class.java
            )

            ClearCacheResponse(
                success = true,
                entriesCleared = 0, // Can't easily get count before delete
                tenantId = tenantId
            )
        } catch (e: Exception) {
            ClearCacheResponse(
                success = false,
                entriesCleared = 0,
                tenantId = tenantId
            )
        }
    }
}
