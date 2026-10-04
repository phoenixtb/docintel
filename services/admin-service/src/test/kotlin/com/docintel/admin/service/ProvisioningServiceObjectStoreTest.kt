package com.docintel.admin.service

import com.docintel.admin.config.ObjectStoreConfig
import com.docintel.admin.config.ObjectStoreProperties
import org.junit.jupiter.api.AfterAll
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertDoesNotThrow
import org.junit.jupiter.api.assertThrows
import org.testcontainers.containers.GenericContainer
import org.testcontainers.containers.wait.strategy.Wait
import org.testcontainers.utility.DockerImageName
import software.amazon.awssdk.core.sync.RequestBody
import software.amazon.awssdk.services.s3.S3Client
import software.amazon.awssdk.services.s3.model.NoSuchBucketException
import java.time.Duration

/**
 * Tenant bucket provisioning against a real S3 server (VersityGW Testcontainer).
 * Plain JUnit — no Spring context; ProvisioningService is built by hand.
 */
class ProvisioningServiceObjectStoreTest {

    companion object {
        // Keep in sync with the object-store service in docker-compose.yml
        // (enforced by scripts/check-object-store-pin.sh).
        private val objectStore: GenericContainer<*> =
            GenericContainer(DockerImageName.parse("versity/versitygw:v1.8.0"))
                .withExposedPorts(7070)
                .withEnv("ROOT_ACCESS_KEY", "test-access")
                .withEnv("ROOT_SECRET_KEY", "test-secret-key")
                .withCommand("--port", ":7070", "--health", "/health", "--quiet", "posix", "/tmp")
                .waitingFor(Wait.forHttp("/health").forPort(7070).forStatusCode(200))
                .also { it.start() }

        private val props = ObjectStoreProperties(
            endpoint = "http://${objectStore.host}:${objectStore.getMappedPort(7070)}",
            accessKey = "test-access",
            secretKey = "test-secret-key",
        )
        private val s3: S3Client = ObjectStoreConfig().s3Client(props)

        @JvmStatic
        @AfterAll
        fun tearDown() {
            s3.close()
            objectStore.stop()
        }
    }

    private val service = ProvisioningService(s3, props, qdrantUrl = "http://localhost:1", embeddingDim = 8)

    @Test
    fun `createTenantBucket creates the tenant bucket`() {
        service.createTenantBucket("t-create")

        assertDoesNotThrow { s3.headBucket { it.bucket("docintel-t-create") } }
    }

    @Test
    fun `createTenantBucket is idempotent`() {
        service.createTenantBucket("t-twice")
        service.createTenantBucket("t-twice")

        assertDoesNotThrow { s3.headBucket { it.bucket("docintel-t-twice") } }
    }

    @Test
    fun `deleteTenantBucket removes an empty bucket`() {
        service.createTenantBucket("t-empty")

        service.deleteTenantBucket("t-empty")

        assertThrows<NoSuchBucketException> { s3.headBucket { it.bucket("docintel-t-empty") } }
    }

    @Test
    fun `deleteTenantBucket leaves a non-empty bucket in place without throwing`() {
        service.createTenantBucket("t-full")
        s3.putObject({ it.bucket("docintel-t-full").key("docs/x/original.txt") }, RequestBody.fromString("x"))

        service.deleteTenantBucket("t-full")

        assertDoesNotThrow { s3.headObject { it.bucket("docintel-t-full").key("docs/x/original.txt") } }
    }

    @Test
    fun `deleteTenantBucket tolerates a bucket that never existed`() {
        assertDoesNotThrow { service.deleteTenantBucket("t-never") }
    }

    @Test
    fun `createTenantBucket does not fail tenant creation when the store is unreachable`() {
        val unreachable = ObjectStoreProperties(
            endpoint = "http://localhost:1",
            accessKey = "a",
            secretKey = "b",
            connectTimeout = Duration.ofMillis(200),
            apiCallTimeout = Duration.ofSeconds(5),
        )
        ObjectStoreConfig().s3Client(unreachable).use { client ->
            val offline = ProvisioningService(client, unreachable, qdrantUrl = "http://localhost:1", embeddingDim = 8)

            assertDoesNotThrow { offline.createTenantBucket("t-offline") }
        }
    }
}
