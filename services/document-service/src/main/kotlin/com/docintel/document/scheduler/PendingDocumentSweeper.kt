package com.docintel.document.scheduler

import com.docintel.document.entity.ProcessingStatus
import com.docintel.document.repository.DocumentRepository
import com.docintel.document.service.DocumentService
import com.docintel.document.tenant.TenantContextHolder
import kotlinx.coroutines.runBlocking
import org.slf4j.LoggerFactory
import org.springframework.data.domain.PageRequest
import org.springframework.scheduling.annotation.Scheduled
import org.springframework.stereotype.Component
import java.time.Instant
import java.time.temporal.ChronoUnit

/**
 * Scheduled job that retries documents stuck in PENDING or PROCESSING state.
 * A document is considered stale if it has been in a non-terminal state for
 * more than [STALE_MINUTES] minutes (e.g. service crashed mid-processing).
 *
 * Runs every 5 minutes. Processes at most [BATCH_SIZE] stale documents per sweep.
 */
@Component
class PendingDocumentSweeper(
    private val documentRepository: DocumentRepository,
    private val documentService: DocumentService,
) {

    private val log = LoggerFactory.getLogger(javaClass)

    companion object {
        private const val STALE_MINUTES = 10L
        private const val BATCH_SIZE = 20
    }

    @Scheduled(fixedDelay = 5 * 60 * 1_000L, initialDelay = 60_000L)
    fun sweep() {
        val before = Instant.now().minus(STALE_MINUTES, ChronoUnit.MINUTES)
        val stale = documentRepository.findStaleByStatusIn(
            statuses = listOf(ProcessingStatus.PENDING, ProcessingStatus.PROCESSING),
            before = before,
            pageable = PageRequest.of(0, BATCH_SIZE),
        )

        if (stale.isEmpty()) return

        log.info("PendingDocumentSweeper: retrying {} stale document(s)", stale.size)
        stale.forEach { doc ->
            try {
                TenantContextHolder.setTenantId(doc.tenantId)
                TenantContextHolder.setUserRole("platform_admin")
                val domainHint = doc.metadata["domain_hint"] ?: "auto"
                runBlocking { documentService.processDocument(doc.id, doc.tenantId, domainHint) }
                log.info("Sweeper: reprocessed document {} (tenant={})", doc.id, doc.tenantId)
            } catch (e: Exception) {
                log.error("Sweeper: failed to reprocess document {}: {}", doc.id, e.message)
            } finally {
                TenantContextHolder.clear()
            }
        }
    }
}
