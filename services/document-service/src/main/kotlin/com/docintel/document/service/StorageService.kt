package com.docintel.document.service

import com.docintel.document.config.ObjectStoreProperties
import com.docintel.document.tenant.TenantContextHolder
import org.slf4j.LoggerFactory
import org.springframework.stereotype.Service
import org.springframework.web.multipart.MultipartFile
import software.amazon.awssdk.core.sync.RequestBody
import software.amazon.awssdk.services.s3.S3Client
import software.amazon.awssdk.services.s3.model.BucketAlreadyOwnedByYouException
import software.amazon.awssdk.services.s3.model.NoSuchBucketException
import software.amazon.awssdk.services.s3.model.ObjectIdentifier
import software.amazon.awssdk.services.s3.model.S3Exception
import java.io.InputStream

/** Some objects under a document's prefix could not be deleted; the caller should retry. */
class ObjectDeletionException(message: String) : RuntimeException(message)

/**
 * Document blobs in the S3-compatible object store.
 *
 * Layout: one bucket per tenant (`docintel-{tenantId}`), content-addressable keys
 * (`docs/{contentHash}/original.{ext}`). Same content → same key, so PUT is idempotent.
 */
@Service
class StorageService(
    private val s3: S3Client,
    private val props: ObjectStoreProperties,
) {

    private val log = LoggerFactory.getLogger(javaClass)

    companion object {
        private val ALLOWED_EXTENSIONS = setOf("pdf", "docx", "doc", "txt", "csv", "md", "rtf", "odt")

        /** DeleteObjects accepts at most 1000 keys per request. */
        private const val DELETE_BATCH_SIZE = 1000

        fun validateFileExtension(filename: String?) {
            val ext = filename?.substringAfterLast('.', "")?.lowercase() ?: ""
            if (ext !in ALLOWED_EXTENSIONS) {
                throw IllegalArgumentException(
                    "File type '.$ext' is not allowed. Supported: ${ALLOWED_EXTENSIONS.joinToString(", ") { ".$it" }}"
                )
            }
        }

        /**
         * Content-addressable object key: docs/{content_hash}/original.{ext}
         *
         * - Encodes identity: same content → same key → PUT is idempotent
         * - Tenant-scoped by bucket, so keys never collide across tenants
         */
        fun contentAddressablePath(contentHash: String, filename: String): String {
            val ext = filename.substringAfterLast('.', "bin")
            return "docs/$contentHash/original.$ext"
        }

        fun bucketFor(tenantId: String): String = "docintel-$tenantId"
    }

    /**
     * Create the tenant bucket unless it exists. HEAD first keeps the common path read-only;
     * losing a create race to another writer ("already owned by you") counts as success.
     */
    private fun ensureBucket(bucket: String) {
        try {
            s3.headBucket { it.bucket(bucket) }
            return
        } catch (e: S3Exception) {
            if (e.statusCode() != 404) throw e
        }
        try {
            s3.createBucket { req ->
                req.bucket(bucket)
                if (props.region != "us-east-1") {
                    req.createBucketConfiguration { it.locationConstraint(props.region) }
                }
            }
            log.info("Created bucket {}", bucket)
        } catch (e: BucketAlreadyOwnedByYouException) {
            log.debug("Bucket {} created concurrently", bucket)
        }
    }

    /**
     * Upload a multipart file at its content-addressable key and return the key.
     * The body is re-read from [MultipartFile.getInputStream] on each SDK retry attempt.
     */
    fun storeFile(file: MultipartFile, tenantId: String, contentHash: String): String {
        validateFileExtension(file.originalFilename)
        val bucket = bucketFor(tenantId)
        ensureBucket(bucket)
        val key = contentAddressablePath(contentHash, file.originalFilename ?: "upload.bin")
        val contentType = file.contentType ?: "application/octet-stream"

        s3.putObject(
            { it.bucket(bucket).key(key).contentType(contentType) },
            RequestBody.fromContentProvider({ file.inputStream }, file.size, contentType),
        )
        return key
    }

    /** Stream an object from the current tenant's bucket. Throws NoSuchKeyException if absent. */
    fun getFile(filePath: String): InputStream {
        val tenantId = TenantContextHolder.getTenantId()
        return s3.getObject { it.bucket(bucketFor(tenantId)).key(filePath) }
    }

    /**
     * Delete every object under the document's key prefix (`docs/{hash}/`), derived from
     * its stored file path. Idempotent: a missing bucket or prefix means nothing is left
     * to delete. Throws [ObjectDeletionException] if any key could not be deleted.
     */
    fun deleteDocumentFiles(tenantId: String, filePath: String) {
        val bucket = bucketFor(tenantId)
        val prefix = filePath.substringBeforeLast('/', filePath) + "/"

        val keys = try {
            s3.listObjectsV2Paginator { it.bucket(bucket).prefix(prefix) }
                .contents()
                .map { ObjectIdentifier.builder().key(it.key()).build() }
        } catch (e: NoSuchBucketException) {
            log.debug("Bucket {} is gone; nothing to delete under {}", bucket, prefix)
            return
        }

        keys.chunked(DELETE_BATCH_SIZE).forEach { batch ->
            val response = s3.deleteObjects { req ->
                req.bucket(bucket).delete { it.objects(batch).quiet(true) }
            }
            if (response.hasErrors() && response.errors().isNotEmpty()) {
                val first = response.errors().first()
                throw ObjectDeletionException(
                    "Failed to delete ${response.errors().size} object(s) in $bucket under $prefix " +
                        "(first: ${first.key()} ${first.code()})"
                )
            }
        }
    }
}
