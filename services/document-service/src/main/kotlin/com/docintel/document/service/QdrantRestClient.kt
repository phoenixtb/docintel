package com.docintel.document.service

import org.slf4j.LoggerFactory
import org.springframework.beans.factory.annotation.Value
import org.springframework.stereotype.Service
import org.springframework.web.client.HttpClientErrorException
import org.springframework.web.client.RestTemplate
import java.util.UUID

/**
 * Thin HTTP client for Qdrant REST API operations used by [ReconciliationSweeper].
 *
 * Only covers the two operations needed for reconciliation:
 *  - Scroll all document_ids from a tenant collection.
 *  - Bulk-delete points whose document_id is in a given orphan set.
 */
@Service
class QdrantRestClient(
    @Value("\${qdrant.url:http://qdrant:6333}") private val qdrantUrl: String,
) {
    private val logger = LoggerFactory.getLogger(javaClass)
    private val rest = RestTemplate()

    /** Returns all unique document_ids present in the tenant's Qdrant collection. */
    fun scrollDocumentIds(tenantId: String): Set<UUID> {
        val collection = "documents_$tenantId"
        val ids = mutableSetOf<UUID>()
        var offset: Any? = null

        while (true) {
            val body = mapOf(
                "limit" to 1000,
                "offset" to offset,
                "with_payload" to listOf("meta.document_id"),
                "with_vector" to false,
            )
            @Suppress("UNCHECKED_CAST")
            val response = rest.postForObject(
                "$qdrantUrl/collections/$collection/points/scroll",
                body,
                Map::class.java,
            ) as? Map<String, Any> ?: break

            @Suppress("UNCHECKED_CAST")
            val result = response["result"] as? Map<String, Any> ?: break

            @Suppress("UNCHECKED_CAST")
            val points = result["points"] as? List<Map<String, Any>> ?: break

            for (point in points) {
                @Suppress("UNCHECKED_CAST")
                val meta = (point["payload"] as? Map<String, Any>)?.get("meta") as? Map<String, Any>
                val docId = meta?.get("document_id") as? String ?: continue
                runCatching { ids.add(UUID.fromString(docId)) }
            }

            offset = result["next_page_offset"]
            if (offset == null) break
        }

        return ids
    }

    /**
     * Delete all Qdrant points whose `meta.document_id` is in [orphanIds].
     * Batched to avoid oversized payloads.
     */
    fun deleteOrphanedPoints(tenantId: String, orphanIds: List<UUID>): Int {
        if (orphanIds.isEmpty()) return 0
        val collection = "documents_$tenantId"
        var total = 0

        for (batch in orphanIds.chunked(200)) {
            val body = mapOf(
                "filter" to mapOf(
                    "must" to listOf(
                        mapOf(
                            "key" to "meta.document_id",
                            "match" to mapOf("any" to batch.map { it.toString() }),
                        )
                    )
                )
            )
            try {
                rest.postForObject("$qdrantUrl/collections/$collection/points/delete", body, Map::class.java)
                total += batch.size
            } catch (e: Exception) {
                logger.warn("QdrantRestClient: batch delete failed for collection {}: {}", collection, e.message)
            }
        }
        return total
    }

    fun collectionExists(tenantId: String): Boolean =
        try {
            rest.getForObject("$qdrantUrl/collections/documents_$tenantId", Map::class.java)
            true
        } catch (e: HttpClientErrorException.NotFound) {
            false
        } catch (e: Exception) {
            logger.warn("QdrantRestClient: could not check collection for tenant {}: {}", tenantId, e.message)
            false
        }
}
