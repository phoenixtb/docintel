package com.docintel.admin.config

import io.minio.MinioClient
import org.springframework.beans.factory.annotation.Value
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration

@Configuration
class MinioConfig(
    @Value("\${minio.endpoint:http://localhost:9000}") private val endpoint: String,
    @Value("\${minio.access-key:minioadmin}") private val accessKey: String,
    @Value("\${minio.secret-key:minioadmin}") private val secretKey: String,
) {
    @Bean
    fun minioClient(): MinioClient =
        MinioClient.builder()
            .endpoint(endpoint)
            .credentials(accessKey, secretKey)
            .build()
}
