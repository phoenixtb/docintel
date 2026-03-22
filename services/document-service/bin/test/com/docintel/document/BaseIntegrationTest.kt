package com.docintel.document

import org.springframework.boot.test.context.SpringBootTest
import org.springframework.boot.test.util.TestPropertyValues
import org.springframework.context.ApplicationContextInitializer
import org.springframework.context.ConfigurableApplicationContext
import org.springframework.test.context.ActiveProfiles
import org.springframework.test.context.ContextConfiguration
import org.testcontainers.containers.GenericContainer
import org.testcontainers.containers.PostgreSQLContainer
import org.testcontainers.utility.DockerImageName

@Suppress("UNCHECKED_CAST")

/**
 * Base class for integration tests that require Testcontainers (PostgreSQL, MinIO).
 *
 * Containers are started eagerly in the companion object init block (class-load time),
 * then wired into the Spring Environment via [Initializer] — which runs before any
 * bean is created, bypassing the @DynamicPropertySource/TestcontainersExtension
 * ordering sensitivity.
 *
 * Set TESTCONTAINERS_ENABLED=true to run these tests.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@ActiveProfiles("test")
@ContextConfiguration(initializers = [BaseIntegrationTest.Initializer::class])
abstract class BaseIntegrationTest {

    /**
     * Wires container coordinates into the Spring Environment before any bean is instantiated.
     * TestPropertyValues has the highest property-source priority (above application-test.yml).
     */
    class Initializer : ApplicationContextInitializer<ConfigurableApplicationContext> {
        override fun initialize(ctx: ConfigurableApplicationContext) {
            val username = postgres.username
            val password = postgres.password
            val minioEndpoint = "http://${minio.host}:${minio.getMappedPort(9000)}"
            val redisHost = redis.host
            val redisPort = redis.getMappedPort(6379)

            // Embed credentials in the JDBC URL so the PostgreSQL driver reads them
            // directly regardless of how HikariCP builds its connection Properties.
            val jdbcUrl = postgres.jdbcUrl
                .replace("?", "?user=$username&password=$password&")

            System.setProperty("spring.datasource.url", jdbcUrl)
            System.setProperty("spring.datasource.username", username)
            System.setProperty("spring.datasource.password", password)
            System.setProperty("spring.datasource.driver-class-name", "org.postgresql.Driver")
            System.setProperty("minio.endpoint", minioEndpoint)

            TestPropertyValues.of(
                "spring.datasource.url=$jdbcUrl",
                "spring.datasource.username=$username",
                "spring.datasource.password=$password",
                "spring.datasource.driver-class-name=org.postgresql.Driver",
                "minio.endpoint=$minioEndpoint",
                "minio.access-key=minioadmin",
                "minio.secret-key=minioadmin",
                "spring.data.redis.host=$redisHost",
                "spring.data.redis.port=$redisPort",
            ).applyTo(ctx.environment)
        }
    }

    companion object {
        val postgres: PostgreSQLContainer<*> =
            PostgreSQLContainer(DockerImageName.parse("postgres:15-alpine"))
                .withDatabaseName("testdb")
                .withUsername("test")
                .withPassword("test")

        val minio: GenericContainer<*> =
            GenericContainer(DockerImageName.parse("minio/minio:RELEASE.2024-01-16T16-07-38Z"))
                .withExposedPorts(9000)
                .withEnv("MINIO_ROOT_USER", "minioadmin")
                .withEnv("MINIO_ROOT_PASSWORD", "minioadmin")
                .withCommand("server /data")

        val redis: GenericContainer<*> =
            GenericContainer(DockerImageName.parse("redis:7.4.0-alpine"))
                .withExposedPorts(6379)

        init {
            postgres.start()
            minio.start()
            redis.start()
        }
    }
}
