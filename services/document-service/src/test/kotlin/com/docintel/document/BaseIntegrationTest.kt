package com.docintel.document

import org.springframework.boot.test.context.SpringBootTest
import org.springframework.boot.test.util.TestPropertyValues
import org.springframework.context.ApplicationContextInitializer
import org.springframework.context.ConfigurableApplicationContext
import org.springframework.test.context.ActiveProfiles
import org.springframework.test.context.ContextConfiguration
import org.testcontainers.containers.GenericContainer
import org.testcontainers.containers.PostgreSQLContainer
import org.testcontainers.containers.wait.strategy.Wait
import org.testcontainers.utility.DockerImageName

@Suppress("UNCHECKED_CAST")

/**
 * Base class for integration tests that require Testcontainers (PostgreSQL, VersityGW S3, Redis).
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
            val objectStoreEndpoint = "http://${objectStore.host}:${objectStore.getMappedPort(7070)}"
            val redisHost = redis.host
            val redisPort = redis.getMappedPort(6379)

            // Embed credentials in the JDBC URL so the PostgreSQL driver reads them
            // directly regardless of how HikariCP builds its connection Properties.
            // Appended (not prepended): TenantDataSourceConfig.flyway() strips
            // [?&]user=/password= and would otherwise swallow the '?' before the
            // container's own parameters (loggerLevel=OFF).
            val jdbcUrl = postgres.jdbcUrl +
                (if ('?' in postgres.jdbcUrl) "&" else "?") + "user=$username&password=$password"

            System.setProperty("spring.datasource.url", jdbcUrl)
            System.setProperty("spring.datasource.username", username)
            System.setProperty("spring.datasource.password", password)
            System.setProperty("spring.datasource.driver-class-name", "org.postgresql.Driver")
            System.setProperty("object-store.endpoint", objectStoreEndpoint)

            TestPropertyValues.of(
                "spring.datasource.url=$jdbcUrl",
                "spring.datasource.username=$username",
                "spring.datasource.password=$password",
                "spring.datasource.driver-class-name=org.postgresql.Driver",
                "object-store.endpoint=$objectStoreEndpoint",
                "object-store.access-key=$OBJECT_STORE_ACCESS_KEY",
                "object-store.secret-key=$OBJECT_STORE_SECRET_KEY",
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
                .withInitScript("db/test-roles.sql")

        const val OBJECT_STORE_ACCESS_KEY = "test-access"
        const val OBJECT_STORE_SECRET_KEY = "test-secret-key"

        // Keep in sync with the object-store service in docker-compose.yml
        // (enforced by scripts/check-object-store-pin.sh).
        val objectStore: GenericContainer<*> =
            GenericContainer(DockerImageName.parse("versity/versitygw:v1.8.0"))
                .withExposedPorts(7070)
                .withEnv("ROOT_ACCESS_KEY", OBJECT_STORE_ACCESS_KEY)
                .withEnv("ROOT_SECRET_KEY", OBJECT_STORE_SECRET_KEY)
                .withCommand("--port", ":7070", "--health", "/health", "--quiet", "posix", "/tmp")
                .waitingFor(Wait.forHttp("/health").forPort(7070).forStatusCode(200))

        val redis: GenericContainer<*> =
            GenericContainer(DockerImageName.parse("redis:7.4.0-alpine"))
                .withExposedPorts(6379)

        init {
            postgres.start()
            objectStore.start()
            redis.start()
        }
    }
}
