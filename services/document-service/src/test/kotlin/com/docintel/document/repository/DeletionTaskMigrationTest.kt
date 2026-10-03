package com.docintel.document.repository

import com.docintel.document.BaseIntegrationTest
import org.junit.jupiter.api.Test
import org.springframework.beans.factory.annotation.Autowired
import org.springframework.jdbc.core.JdbcTemplate
import kotlin.test.assertTrue

/** Flyway V6 renames the deletion task's object-store flag in the migrated `documents` schema. */
class DeletionTaskMigrationTest : BaseIntegrationTest() {

    @Autowired
    private lateinit var jdbcTemplate: JdbcTemplate

    @Test
    fun `deletion_tasks exposes object_store_done and no longer minio_done`() {
        val columns = jdbcTemplate.queryForList(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'documents' AND table_name = 'deletion_tasks'
            """.trimIndent(),
            String::class.java,
        )

        assertTrue("object_store_done" in columns, "columns were $columns")
        assertTrue("minio_done" !in columns, "columns were $columns")
    }
}
