package com.docintel.document.service

import com.docintel.document.dto.CleanupFiltersRequest
import com.docintel.document.dto.CleanupJobStatus
import com.docintel.document.entity.ProcessingStatus
import com.docintel.document.service.cleanup.CleanupJob
import com.docintel.document.service.cleanup.CleanupJobRegistry
import com.docintel.document.service.cleanup.CleanupJobService
import com.docintel.document.sse.CleanupSseRegistry
import io.mockk.*
import io.mockk.impl.annotations.MockK
import io.mockk.junit5.MockKExtension
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.runTest
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import org.junit.jupiter.api.extension.ExtendWith
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.test.assertEquals
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

@ExtendWith(MockKExtension::class)
class CleanupJobServiceTest {

    @MockK private lateinit var documentService: DocumentService
    @MockK private lateinit var sseRegistry: CleanupSseRegistry

    private lateinit var jobRegistry: CleanupJobRegistry
    private lateinit var service: CleanupJobService

    private val tenant = "test-tenant"
    private val noopFilters = CleanupFiltersRequest()

    @BeforeEach
    fun setUp() {
        jobRegistry = CleanupJobRegistry()
        service = CleanupJobService(documentService, jobRegistry, sseRegistry)
        justRun { sseRegistry.sendProgress(any()) }
        justRun { sseRegistry.sendComplete(any()) }
    }

    @Test
    fun `startJob returns response with matchCount and QUEUED status`() {
        every { documentService.previewCleanup(tenant, noopFilters) } returns 5
        every { documentService.snapshotMatchingIds(tenant, noopFilters) } returns emptyList()

        val response = service.startJob(tenant, noopFilters)

        assertEquals(5, response.matchCount)
        assertEquals(CleanupJobStatus.QUEUED, response.status)
        assertEquals(tenant, response.tenantId)
        assertNotNull(response.jobId)
    }

    @Test
    fun `startJob throws when another job is already active`() {
        every { documentService.previewCleanup(tenant, noopFilters) } returns 1
        every { documentService.snapshotMatchingIds(tenant, noopFilters) } returns emptyList()

        service.startJob(tenant, noopFilters)

        // Second start should fail — first job is still registered as active initially
        val ex = assertThrows<IllegalStateException> {
            service.startJob(tenant, noopFilters)
        }
        assertTrue(ex.message!!.contains("already active"))
    }

    @Test
    fun `cancelJob sets cancelRequested flag`() {
        // Hold the job inside its snapshot step so it is still active when cancel arrives;
        // with nothing to delete it would otherwise finish first on a slow machine.
        val release = CountDownLatch(1)
        every { documentService.previewCleanup(tenant, noopFilters) } returns 0
        every { documentService.snapshotMatchingIds(tenant, noopFilters) } answers {
            release.await(5, TimeUnit.SECONDS)
            emptyList()
        }

        val response = service.startJob(tenant, noopFilters)
        try {
            assertTrue(service.cancelJob(response.jobId, tenant))
        } finally {
            release.countDown()
        }
    }

    @Test
    fun `getJobStatus returns null for unknown job`() {
        assertNull(service.getJobStatus(UUID.randomUUID(), tenant))
    }

    @Test
    fun `getJobStatus returns null for job belonging to different tenant`() {
        every { documentService.previewCleanup(tenant, noopFilters) } returns 0
        every { documentService.snapshotMatchingIds(tenant, noopFilters) } returns emptyList()

        val response = service.startJob(tenant, noopFilters)
        assertNull(service.getJobStatus(response.jobId, "other-tenant"))
    }
}
