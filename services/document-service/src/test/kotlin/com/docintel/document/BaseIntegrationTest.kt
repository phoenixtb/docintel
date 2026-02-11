package com.docintel.document

import org.junit.jupiter.api.BeforeAll
import org.springframework.boot.test.context.SpringBootTest
import org.springframework.test.context.ActiveProfiles
import org.springframework.test.context.DynamicPropertyRegistry
import org.springframework.test.context.DynamicPropertySource
import org.testcontainers.containers.GenericContainer
import org.testcontainers.containers.PostgreSQLContainer
import org.testcontainers.junit.jupiter.Container
import org.testcontainers.junit.jupiter.Testcontainers
import org.testcontainers.utility.DockerImageName

/**
 * Base class for integration tests with Testcontainers.
 * Provides PostgreSQL and MinIO containers.
 */
/**
 * Base class for integration tests that require Testcontainers (PostgreSQL, MinIO).
 * 
 * To run these tests, Docker must be running and properly configured.
 * If Docker is not available, tests extending this class will be skipped.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@ActiveProfiles("test")
@Testcontainers
@org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable(
    named = "TESTCONTAINERS_ENABLED",
    matches = "true",
    disabledReason = "Testcontainers require Docker. Set TESTCONTAINERS_ENABLED=true to run."
)
abstract class BaseIntegrationTest {

    companion object {
        @Container
        @JvmStatic
        val postgresContainer: PostgreSQLContainer<*> = PostgreSQLContainer(DockerImageName.parse("postgres:15-alpine"))
            .withDatabaseName("testdb")
            .withUsername("test")
            .withPassword("test")

        @Container
        @JvmStatic
        val minioContainer: GenericContainer<*> = GenericContainer(DockerImageName.parse("minio/minio:RELEASE.2024-01-16T16-07-38Z"))
            .withExposedPorts(9000)
            .withEnv("MINIO_ROOT_USER", "minioadmin")
            .withEnv("MINIO_ROOT_PASSWORD", "minioadmin")
            .withCommand("server /data")

        @DynamicPropertySource
        @JvmStatic
        fun configureProperties(registry: DynamicPropertyRegistry) {
            // PostgreSQL
            registry.add("spring.datasource.url") { postgresContainer.jdbcUrl }
            registry.add("spring.datasource.username") { postgresContainer.username }
            registry.add("spring.datasource.password") { postgresContainer.password }
            registry.add("spring.datasource.driver-class-name") { "org.postgresql.Driver" }

            // MinIO
            registry.add("minio.endpoint") { 
                "http://${minioContainer.host}:${minioContainer.getMappedPort(9000)}" 
            }
            registry.add("minio.access-key") { "minioadmin" }
            registry.add("minio.secret-key") { "minioadmin" }
        }
    }
}
