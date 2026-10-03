package com.docintel.admin.config

import jakarta.validation.constraints.NotBlank
import jakarta.validation.constraints.Pattern
import org.springframework.boot.context.properties.ConfigurationProperties
import org.springframework.validation.annotation.Validated
import java.time.Duration

/**
 * Connection settings for the S3-compatible object store (OBJECT_STORE_* env).
 * Validated at startup: a missing endpoint or credential fails the boot, not the first upload.
 *
 * The server behind [endpoint] is a deployment choice (VersityGW locally, AWS S3 / R2 /
 * Ceph RGW in production); nothing else in the service depends on it.
 */
@Validated
@ConfigurationProperties(prefix = "object-store")
data class ObjectStoreProperties(
    @field:Pattern(regexp = "https?://.+", message = "must be an http(s) URL")
    val endpoint: String,
    @field:NotBlank
    val region: String = "us-east-1",
    @field:NotBlank
    val accessKey: String,
    @field:NotBlank
    val secretKey: String,
    /** Path-style addressing (`host/bucket/key`); required by most self-hosted servers. */
    val forcePathStyle: Boolean = true,
    val connectTimeout: Duration = Duration.ofSeconds(5),
    /** Max silence on an open socket before the attempt fails. */
    val socketTimeout: Duration = Duration.ofSeconds(60),
    /** Upper bound for one call including retries. */
    val apiCallTimeout: Duration = Duration.ofSeconds(30),
) {
    override fun toString(): String =
        "ObjectStoreProperties(endpoint=$endpoint, region=$region, forcePathStyle=$forcePathStyle)"
}
