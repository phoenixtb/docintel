package com.docintel.document.service

import com.docintel.document.tenant.TenantContextHolder
import io.minio.*
import org.slf4j.LoggerFactory
import org.springframework.stereotype.Service
import org.springframework.web.multipart.MultipartFile
import java.io.InputStream
import java.util.UUID

@Service
class StorageService(private val minioClient: MinioClient) {

    private val log = LoggerFactory.getLogger(javaClass)

    companion object {
        private val ALLOWED_EXTENSIONS = setOf("pdf", "docx", "doc", "txt", "csv", "md", "rtf", "odt")

        fun validateFileExtension(filename: String?) {
            val ext = filename?.substringAfterLast('.', "")?.lowercase() ?: ""
            if (ext !in ALLOWED_EXTENSIONS) {
                throw IllegalArgumentException(
                    "File type '.$ext' is not allowed. Supported: ${ALLOWED_EXTENSIONS.joinToString(", ") { ".$it" }}"
                )
            }
        }

        /**
         * Content-addressable MinIO object path: {tenant_id}/docs/{content_hash}/original.{ext}
         *
         * - Encodes identity: same content → same path → MinIO PUT is idempotent
         * - Tenant-scoped: no cross-tenant path collisions
         */
        fun contentAddressablePath(contentHash: String, filename: String): String {
            val ext = filename.substringAfterLast('.', "bin")
            return "docs/$contentHash/original.$ext"
        }
    }

    private fun bucketFor(tenantId: String): String = "docintel-$tenantId"

    private fun ensureBucket(tenantId: String) {
        val bucket = bucketFor(tenantId)
        try {
            val exists = minioClient.bucketExists(BucketExistsArgs.builder().bucket(bucket).build())
            if (!exists) {
                minioClient.makeBucket(MakeBucketArgs.builder().bucket(bucket).build())
                log.info("Created MinIO bucket: {}", bucket)
            }
        } catch (e: Exception) {
            log.warn("Could not ensure MinIO bucket {}: {}", bucket, e.message)
        }
    }

    /**
     * Upload a multipart file using the content-addressable path convention.
     * Returns the MinIO object path (relative to bucket).
     *
     * MinIO PUT is idempotent: if the same hash already exists, this is a no-op.
     */
    fun storeFile(file: MultipartFile, tenantId: String, contentHash: String): String {
        validateFileExtension(file.originalFilename)
        ensureBucket(tenantId)
        val objectName = contentAddressablePath(contentHash, file.originalFilename ?: "upload.bin")

        minioClient.putObject(
            PutObjectArgs.builder()
                .bucket(bucketFor(tenantId))
                .`object`(objectName)
                .stream(file.inputStream, file.size, -1)
                .contentType(file.contentType ?: "application/octet-stream")
                .build()
        )
        return objectName
    }

    fun getFile(filePath: String): InputStream {
        val tenantId = TenantContextHolder.getTenantId()
        return minioClient.getObject(
            GetObjectArgs.builder()
                .bucket(bucketFor(tenantId))
                .`object`(filePath)
                .build()
        )
    }

    fun deleteFile(filePath: String) {
        val tenantId = TenantContextHolder.getTenantId()
        minioClient.removeObject(
            RemoveObjectArgs.builder()
                .bucket(bucketFor(tenantId))
                .`object`(filePath)
                .build()
        )
    }

    /**
     * Delete all MinIO objects under the content-addressable prefix for a document.
     * The prefix is derived from the document's file_path (which already encodes the hash).
     */
    fun deleteDocumentFiles(tenantId: String, filePath: String) {
        val bucket = bucketFor(tenantId)
        // file_path is e.g. "docs/{hash}/original.pdf" — strip the filename to get the directory
        val prefix = filePath.substringBeforeLast('/', filePath) + "/"

        val objects = minioClient.listObjects(
            ListObjectsArgs.builder()
                .bucket(bucket)
                .prefix(prefix)
                .recursive(true)
                .build()
        )

        objects.forEach { result ->
            val obj = result.get()
            minioClient.removeObject(
                RemoveObjectArgs.builder()
                    .bucket(bucket)
                    .`object`(obj.objectName())
                    .build()
            )
        }
    }
}
