package com.docintel.admin.config

import org.springframework.boot.context.properties.EnableConfigurationProperties
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration
import software.amazon.awssdk.auth.credentials.AwsBasicCredentials
import software.amazon.awssdk.auth.credentials.StaticCredentialsProvider
import software.amazon.awssdk.core.retry.RetryMode
import software.amazon.awssdk.http.apache.ApacheHttpClient
import software.amazon.awssdk.regions.Region
import software.amazon.awssdk.services.s3.S3Client
import java.net.URI

/**
 * S3Client for the object store, used to provision and remove per-tenant buckets
 * (document-service also creates a tenant's bucket lazily on first upload).
 *
 * Retries use the SDK "standard" mode: throttling and transient errors only, exponential
 * backoff with jitter, 3 attempts; client errors (4xx) fail immediately.
 */
@Configuration
@EnableConfigurationProperties(ObjectStoreProperties::class)
class ObjectStoreConfig {

    @Bean(destroyMethod = "close")
    fun s3Client(props: ObjectStoreProperties): S3Client =
        S3Client.builder()
            .endpointOverride(URI.create(props.endpoint))
            .region(Region.of(props.region))
            .credentialsProvider(
                StaticCredentialsProvider.create(AwsBasicCredentials.create(props.accessKey, props.secretKey))
            )
            .forcePathStyle(props.forcePathStyle)
            .httpClientBuilder(
                ApacheHttpClient.builder()
                    .connectionTimeout(props.connectTimeout)
                    .socketTimeout(props.socketTimeout)
            )
            .overrideConfiguration { it.apiCallTimeout(props.apiCallTimeout).retryStrategy(RetryMode.STANDARD) }
            .build()
}
