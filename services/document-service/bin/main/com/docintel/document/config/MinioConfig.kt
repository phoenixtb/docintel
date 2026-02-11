package com.docintel.document.config

import io.minio.BucketExistsArgs
import io.minio.MakeBucketArgs
import io.minio.MinioClient
import org.springframework.beans.factory.annotation.Value
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration

@Configuration
class MinioConfig(
    @Value("\${minio.endpoint:http://localhost:9000}") 
    private val endpoint: String,
    
    @Value("\${minio.access-key:minioadmin}") 
    private val accessKey: String,
    
    @Value("\${minio.secret-key:minioadmin}") 
    private val secretKey: String,
    
    @Value("\${minio.bucket:documents}") 
    private val bucket: String
) {
    @Bean
    fun minioClient(): MinioClient {
        val client = MinioClient.builder()
            .endpoint(endpoint)
            .credentials(accessKey, secretKey)
            .build()
        
        // Initialize bucket
        initBucket(client)
        
        return client
    }

    private fun initBucket(client: MinioClient) {
        try {
            val exists = client.bucketExists(
                BucketExistsArgs.builder().bucket(bucket).build()
            )
            if (!exists) {
                client.makeBucket(
                    MakeBucketArgs.builder().bucket(bucket).build()
                )
            }
        } catch (e: Exception) {
            // Log but don't fail - bucket might be created later
            println("Warning: Could not initialize MinIO bucket: ${e.message}")
        }
    }
}
