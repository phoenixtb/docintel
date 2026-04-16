package com.docintel.admin.service

import com.docintel.admin.dto.UpdateUserPreferencesRequest
import com.docintel.admin.dto.UserPreferences
import com.fasterxml.jackson.databind.ObjectMapper
import org.slf4j.LoggerFactory
import org.springframework.jdbc.core.ConnectionCallback
import org.springframework.jdbc.core.JdbcTemplate
import org.springframework.stereotype.Service
import org.springframework.transaction.annotation.Transactional

@Service
class UserPreferencesService(
    private val jdbc: JdbcTemplate,
    private val objectMapper: ObjectMapper,
) {
    private val log = LoggerFactory.getLogger(UserPreferencesService::class.java)

    @Transactional(readOnly = true)
    fun getPreferences(userId: String, tenantId: String): UserPreferences {
        val thinkingMode = jdbc.execute(ConnectionCallback { conn ->
            conn.createStatement().use { stmt ->
                stmt.execute("SET LOCAL app.current_user_id = '${userId.replace("'", "''")}'")
                stmt.execute("SET LOCAL app.current_tenant  = '${tenantId.replace("'", "''")}'")
            }
            conn.prepareStatement(
                "SELECT value FROM admin.user_preferences WHERE user_id = ? AND tenant_id = ? AND key = 'thinking_mode'"
            ).use { ps ->
                ps.setString(1, userId)
                ps.setString(2, tenantId)
                ps.executeQuery().use { rs ->
                    if (rs.next()) objectMapper.readTree(rs.getString(1))?.asBoolean() ?: false
                    else false
                }
            }
        }) ?: false

        return UserPreferences(thinkingMode = thinkingMode)
    }

    @Transactional
    fun updatePreferences(
        userId: String,
        tenantId: String,
        req: UpdateUserPreferencesRequest,
    ): UserPreferences {
        if (req.thinkingMode != null) {
            jdbc.execute(ConnectionCallback<Unit> { conn ->
                conn.createStatement().use { stmt ->
                    stmt.execute("SET LOCAL app.current_user_id = '${userId.replace("'", "''")}'")
                    stmt.execute("SET LOCAL app.current_tenant  = '${tenantId.replace("'", "''")}'")
                }
                conn.prepareStatement(
                    """
                    INSERT INTO admin.user_preferences (user_id, tenant_id, key, value, updated_at)
                    VALUES (?, ?, 'thinking_mode', ?::jsonb, NOW())
                    ON CONFLICT (user_id, tenant_id, key) DO UPDATE
                      SET value = EXCLUDED.value, updated_at = NOW()
                    """.trimIndent()
                ).use { ps ->
                    ps.setString(1, userId)
                    ps.setString(2, tenantId)
                    ps.setString(3, req.thinkingMode.toString())
                    ps.executeUpdate()
                    Unit
                }
            })
            log.info("User {} (tenant {}) preferences updated — thinking_mode={}", userId, tenantId, req.thinkingMode)
        }

        return getPreferences(userId, tenantId)
    }
}
