package com.docintel.document.config

import org.junit.jupiter.api.Test
import org.springframework.boot.autoconfigure.AutoConfigurations
import org.springframework.boot.autoconfigure.validation.ValidationAutoConfiguration
import org.springframework.boot.test.context.runner.ApplicationContextRunner
import software.amazon.awssdk.services.s3.S3Client
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

/** Startup validation of OBJECT_STORE_* settings — no network, no containers. */
class ObjectStoreConfigTest {

    private val runner = ApplicationContextRunner()
        .withConfiguration(AutoConfigurations.of(ValidationAutoConfiguration::class.java))
        .withUserConfiguration(ObjectStoreConfig::class.java)

    private val valid = arrayOf(
        "object-store.endpoint=http://object-store:7070",
        "object-store.access-key=access",
        "object-store.secret-key=s3cr3t-value",
    )

    @Test
    fun `builds an S3 client from valid settings`() {
        runner.withPropertyValues(*valid).run { ctx ->
            assertNotNull(ctx.getBean(S3Client::class.java))
        }
    }

    @Test
    fun `refuses to start without a secret key`() {
        runner.withPropertyValues(
            "object-store.endpoint=http://object-store:7070",
            "object-store.access-key=a",
            "object-store.secret-key=",
        ).run { ctx -> assertNotNull(ctx.startupFailure) }
    }

    @Test
    fun `refuses to start when the endpoint is not an http URL`() {
        runner.withPropertyValues(
            "object-store.endpoint=object-store:7070",
            "object-store.access-key=a",
            "object-store.secret-key=b",
        ).run { ctx -> assertNotNull(ctx.startupFailure) }
    }

    @Test
    fun `toString never reveals credentials`() {
        runner.withPropertyValues(*valid).run { ctx ->
            val rendered = ctx.getBean(ObjectStoreProperties::class.java).toString()
            assertFalse("s3cr3t-value" in rendered)
            assertTrue("object-store:7070" in rendered)
        }
    }
}
