package com.docintel.admin.service

import io.minio.BucketExistsArgs
import io.minio.MakeBucketArgs
import io.minio.MinioClient
import io.minio.RemoveBucketArgs
import org.slf4j.LoggerFactory
import org.springframework.beans.factory.annotation.Value
import org.springframework.stereotype.Service
import org.springframework.web.client.RestTemplate
import org.springframework.http.HttpEntity
import org.springframework.http.HttpHeaders
import org.springframework.http.MediaType

/**
 * Provisions and deprovisions per-tenant Qdrant collections and MinIO buckets.
 * Called by TenantManagementService on tenant create/delete.
 */
@Service
class ProvisioningService(
    private val minioClient: MinioClient,
    @Value("\${qdrant.url:http://localhost:6333}") private val qdrantUrl: String,
    @Value("\${qdrant.embedding-dim:768}") private val embeddingDim: Int,
) {
    private val log = LoggerFactory.getLogger(javaClass)
    private val restTemplate = RestTemplate()

    // -------------------------------------------------------------------------
    // Qdrant
    // -------------------------------------------------------------------------

    fun createQdrantCollection(tenantId: String) {
        val collectionName = "documents_$tenantId"
        val headers = HttpHeaders().apply { contentType = MediaType.APPLICATION_JSON }
        val body = """
            {
                "vectors": {
                    "": { "size": $embeddingDim, "distance": "Cosine", "on_disk": true }
                },
                "sparse_vectors": {
                    "sparse": { "index": { "on_disk": true } }
                },
                "hnsw_config": { "m": 16, "ef_construct": 100 },
                "on_disk_payload": true
            }
        """.trimIndent()
        try {
            restTemplate.put(
                "$qdrantUrl/collections/$collectionName",
                HttpEntity(body, headers),
            )
            // Create payload index for document_type filtering
            val indexBody = """{"field_name": "meta.document_type", "field_schema": "keyword"}"""
            restTemplate.put(
                "$qdrantUrl/collections/$collectionName/index",
                HttpEntity(indexBody, headers),
            )
            log.info("Created Qdrant collection: {}", collectionName)
        } catch (e: Exception) {
            log.warn("Could not create Qdrant collection {} (may already exist): {}", collectionName, e.message)
        }
    }

    fun deleteQdrantCollection(tenantId: String) {
        val collectionName = "documents_$tenantId"
        try {
            restTemplate.delete("$qdrantUrl/collections/$collectionName")
            log.info("Deleted Qdrant collection: {}", collectionName)
        } catch (e: Exception) {
            log.warn("Could not delete Qdrant collection {}: {}", collectionName, e.message)
        }
    }

    // -------------------------------------------------------------------------
    // MinIO
    // -------------------------------------------------------------------------

    fun createMinioBucket(tenantId: String) {
        val bucket = "docintel-$tenantId"
        try {
            val exists = minioClient.bucketExists(BucketExistsArgs.builder().bucket(bucket).build())
            if (!exists) {
                minioClient.makeBucket(MakeBucketArgs.builder().bucket(bucket).build())
                log.info("Created MinIO bucket: {}", bucket)
            }
        } catch (e: Exception) {
            log.warn("Could not create MinIO bucket {}: {}", bucket, e.message)
        }
    }

    fun deleteMinioBucket(tenantId: String) {
        val bucket = "docintel-$tenantId"
        try {
            minioClient.removeBucket(RemoveBucketArgs.builder().bucket(bucket).build())
            log.info("Deleted MinIO bucket: {}", bucket)
        } catch (e: Exception) {
            log.warn("Could not delete MinIO bucket {} (may have objects): {}", bucket, e.message)
        }
    }
}
