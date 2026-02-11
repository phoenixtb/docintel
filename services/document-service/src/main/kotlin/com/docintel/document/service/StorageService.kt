package com.docintel.document.service

import io.minio.*
import org.springframework.beans.factory.annotation.Value
import org.springframework.stereotype.Service
import org.springframework.web.multipart.MultipartFile
import java.io.InputStream
import java.util.UUID

@Service
class StorageService(
    private val minioClient: MinioClient,
    
    @Value("\${minio.bucket:documents}")
    private val bucket: String
) {
    /**
     * Store a file in MinIO.
     * Path format: {tenant_id}/{document_id}/original.{ext}
     */
    fun storeFile(
        file: MultipartFile,
        tenantId: String,
        documentId: UUID
    ): String {
        val extension = file.originalFilename?.substringAfterLast('.', "bin") ?: "bin"
        val objectName = "$tenantId/$documentId/original.$extension"

        minioClient.putObject(
            PutObjectArgs.builder()
                .bucket(bucket)
                .`object`(objectName)
                .stream(file.inputStream, file.size, -1)
                .contentType(file.contentType ?: "application/octet-stream")
                .build()
        )

        return objectName
    }

    /**
     * Get file as InputStream.
     */
    fun getFile(filePath: String): InputStream {
        return minioClient.getObject(
            GetObjectArgs.builder()
                .bucket(bucket)
                .`object`(filePath)
                .build()
        )
    }

    /**
     * Delete a file from storage.
     */
    fun deleteFile(filePath: String) {
        minioClient.removeObject(
            RemoveObjectArgs.builder()
                .bucket(bucket)
                .`object`(filePath)
                .build()
        )
    }

    /**
     * Delete all files for a document.
     */
    fun deleteDocumentFiles(tenantId: String, documentId: UUID) {
        val prefix = "$tenantId/$documentId/"
        
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
