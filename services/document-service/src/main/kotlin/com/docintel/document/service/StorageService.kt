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

    fun storeFile(file: MultipartFile, tenantId: String, documentId: UUID): String {
        validateFileExtension(file.originalFilename)
        ensureBucket(tenantId)
        val extension = file.originalFilename?.substringAfterLast('.', "bin") ?: "bin"
        val objectName = "$documentId/original.$extension"

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

    fun deleteDocumentFiles(tenantId: String, documentId: UUID) {
        val bucket = bucketFor(tenantId)
        val prefix = "$documentId/"

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
