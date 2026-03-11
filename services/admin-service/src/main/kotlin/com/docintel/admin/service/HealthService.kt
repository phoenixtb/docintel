package com.docintel.admin.service

import com.docintel.admin.dto.ComponentHealth
import com.docintel.admin.dto.HealthStatus
import com.docintel.admin.dto.SystemHealth
import org.springframework.beans.factory.annotation.Value
import org.springframework.data.redis.core.StringRedisTemplate
import org.springframework.stereotype.Service
import java.net.HttpURLConnection
import java.net.URI
import javax.sql.DataSource

@Service
class HealthService(
    private val dataSource: DataSource,
    private val redisTemplate: StringRedisTemplate,

    @Value("\${qdrant.url:http://localhost:6333}")
    private val qdrantUrl: String,
) {
    fun checkSystemHealth(): SystemHealth {
        val components = mutableMapOf<String, ComponentHealth>()
        var overallStatus = HealthStatus.UP

        // Check PostgreSQL
        val pgHealth = checkPostgres()
        components["postgres"] = pgHealth
        if (pgHealth.status != HealthStatus.UP) {
            overallStatus = HealthStatus.DEGRADED
        }

        // Check Qdrant
        val qdrantHealth = checkQdrant()
        components["qdrant"] = qdrantHealth
        if (qdrantHealth.status != HealthStatus.UP) {
            overallStatus = HealthStatus.DEGRADED
        }

        // Check Redis
        val redisHealth = checkRedis()
        components["redis"] = redisHealth
        if (redisHealth.status != HealthStatus.UP) {
            overallStatus = HealthStatus.DEGRADED
        }

        return SystemHealth(
            status = overallStatus,
            components = components
        )
    }

    private fun checkPostgres(): ComponentHealth {
        return try {
            val start = System.currentTimeMillis()
            dataSource.connection.use { conn ->
                conn.createStatement().use { stmt ->
                    stmt.executeQuery("SELECT 1")
                }
            }
            val latency = System.currentTimeMillis() - start
            ComponentHealth(
                name = "PostgreSQL",
                status = HealthStatus.UP,
                latencyMs = latency
            )
        } catch (e: Exception) {
            ComponentHealth(
                name = "PostgreSQL",
                status = HealthStatus.DOWN,
                message = e.message
            )
        }
    }

    private fun checkQdrant(): ComponentHealth {
        return try {
            val start = System.currentTimeMillis()
            val url = URI("$qdrantUrl/healthz").toURL()
            val conn = url.openConnection() as HttpURLConnection
            conn.requestMethod = "GET"
            conn.connectTimeout = 5000
            conn.readTimeout = 5000
            
            val responseCode = conn.responseCode
            val latency = System.currentTimeMillis() - start
            
            if (responseCode == 200) {
                ComponentHealth(
                    name = "Qdrant",
                    status = HealthStatus.UP,
                    latencyMs = latency
                )
            } else {
                ComponentHealth(
                    name = "Qdrant",
                    status = HealthStatus.DOWN,
                    message = "HTTP $responseCode"
                )
            }
        } catch (e: Exception) {
            ComponentHealth(
                name = "Qdrant",
                status = HealthStatus.DOWN,
                message = e.message
            )
        }
    }

    private fun checkRedis(): ComponentHealth {
        return try {
            val start = System.currentTimeMillis()
            redisTemplate.execute { conn -> conn.ping() }
            val latency = System.currentTimeMillis() - start
            ComponentHealth(name = "Redis", status = HealthStatus.UP, latencyMs = latency)
        } catch (e: Exception) {
            ComponentHealth(name = "Redis", status = HealthStatus.DOWN, message = e.message)
        }
    }
}
