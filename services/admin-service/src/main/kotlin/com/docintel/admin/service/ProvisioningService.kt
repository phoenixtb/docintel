package com.docintel.admin.service

import com.docintel.admin.config.ObjectStoreProperties
import org.slf4j.LoggerFactory
import org.springframework.beans.factory.annotation.Value
import org.springframework.http.client.SimpleClientHttpRequestFactory
import org.springframework.stereotype.Service
import org.springframework.web.client.RestTemplate
import org.springframework.http.HttpEntity
import org.springframework.http.HttpHeaders
import org.springframework.http.MediaType
import software.amazon.awssdk.core.exception.SdkException
import software.amazon.awssdk.services.s3.S3Client
import software.amazon.awssdk.services.s3.model.BucketAlreadyOwnedByYouException
import software.amazon.awssdk.services.s3.model.NoSuchBucketException
import software.amazon.awssdk.services.s3.model.S3Exception

/**
 * Provisions and deprovisions per-tenant Qdrant collections and object-store buckets.
 * Called by TenantManagementService on tenant create/delete.
 */
@Service
class ProvisioningService(
    private val s3: S3Client,
    private val objectStore: ObjectStoreProperties,
    @Value("\${qdrant.url:http://localhost:6333}") private val qdrantUrl: String,
    @Value("\${qdrant.embedding-dim:768}") private val embeddingDim: Int,
) {
    private val log = LoggerFactory.getLogger(javaClass)
    private val restTemplate = RestTemplate(SimpleClientHttpRequestFactory().apply {
        setConnectTimeout(5000)
        setReadTimeout(10000)
    })

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
    // Object store (S3-compatible)
    // -------------------------------------------------------------------------

    /**
     * Best-effort: document-service creates the bucket on first upload anyway, so a failure
     * here must not fail tenant creation.
     */
    fun createTenantBucket(tenantId: String) {
        val bucket = tenantBucket(tenantId)
        try {
            s3.createBucket { req ->
                req.bucket(bucket)
                if (objectStore.region != "us-east-1") {
                    req.createBucketConfiguration { it.locationConstraint(objectStore.region) }
                }
            }
            log.info("Created bucket {}", bucket)
        } catch (e: BucketAlreadyOwnedByYouException) {
            log.debug("Bucket {} already exists", bucket)
        } catch (e: SdkException) {
            log.warn("Could not create bucket {}: {}", bucket, e.message)
        }
    }

    /**
     * Remove the tenant bucket. S3 refuses to delete a non-empty bucket; that case is logged
     * and left for an operator (documents are deleted asynchronously by document-service).
     */
    fun deleteTenantBucket(tenantId: String) {
        val bucket = tenantBucket(tenantId)
        try {
            s3.deleteBucket { it.bucket(bucket) }
            log.info("Deleted bucket {}", bucket)
        } catch (e: NoSuchBucketException) {
            log.debug("Bucket {} already absent", bucket)
        } catch (e: S3Exception) {
            if (e.awsErrorDetails()?.errorCode() == "BucketNotEmpty") {
                log.warn("Bucket {} not deleted: it still contains objects", bucket)
            } else {
                log.warn("Could not delete bucket {}: {}", bucket, e.message)
            }
        } catch (e: SdkException) {
            log.warn("Could not delete bucket {}: {}", bucket, e.message)
        }
    }

    private fun tenantBucket(tenantId: String) = "docintel-$tenantId"
}
