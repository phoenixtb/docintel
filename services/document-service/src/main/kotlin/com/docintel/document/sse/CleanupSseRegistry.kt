package com.docintel.document.sse

import com.docintel.document.dto.CleanupJobStatusResponse
import com.docintel.document.service.cleanup.CleanupJob
import org.slf4j.LoggerFactory
import org.springframework.stereotype.Component
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicLong

/**
 * SSE registry for cleanup job progress streams.
 * Keyed by jobId; each job may have multiple connected clients (e.g. multiple browser tabs).
 */
@Component
class CleanupSseRegistry {

    private val logger = LoggerFactory.getLogger(javaClass)
    private val emitters = ConcurrentHashMap<String, CopyOnWriteArrayList<SseEmitter>>()
    private val seq = AtomicLong(0)

    fun register(jobId: String): SseEmitter {
        val emitter = SseEmitter(Long.MAX_VALUE)
        emitters.getOrPut(jobId) { CopyOnWriteArrayList() }.add(emitter)
        val cleanup = Runnable { remove(jobId, emitter) }
        emitter.onCompletion(cleanup)
        emitter.onTimeout(cleanup)
        emitter.onError { cleanup.run() }
        logger.debug("Cleanup SSE client registered for jobId={}", jobId)
        return emitter
    }

    fun sendProgress(job: CleanupJob) {
        val payload = mapOf(
            "jobId"     to job.jobId.toString(),
            "status"    to job.status.get().name,
            "total"     to job.total.get(),
            "processed" to job.processed.get(),
            "succeeded" to job.succeeded.get(),
            "failed"    to job.failed.get(),
        )
        broadcast(job.jobId.toString(), "cleanup_progress", payload)
    }

    fun sendComplete(job: CleanupJob) {
        val payload = mapOf(
            "jobId"       to job.jobId.toString(),
            "status"      to job.status.get().name,
            "total"       to job.total.get(),
            "processed"   to job.processed.get(),
            "succeeded"   to job.succeeded.get(),
            "failed"      to job.failed.get(),
            "errors"      to job.errors(),
            "completedAt" to job.completedAt?.toString(),
        )
        broadcast(job.jobId.toString(), "cleanup_complete", payload)
        closeAll(job.jobId.toString())
    }

    /**
     * Send the terminal state immediately to a single late-connecting emitter.
     * Called when the client subscribes after the job already finished.
     */
    fun replayFinal(jobId: String, status: CleanupJobStatusResponse, emitter: SseEmitter) {
        val payload = mapOf(
            "jobId"       to status.jobId.toString(),
            "status"      to status.status.name,
            "total"       to status.total,
            "processed"   to status.processed,
            "succeeded"   to status.succeeded,
            "failed"      to status.failed,
            "errors"      to status.errors,
            "completedAt" to status.completedAt?.toString(),
        )
        try {
            emitter.send(
                SseEmitter.event()
                    .id(seq.incrementAndGet().toString())
                    .name("cleanup_complete")
                    .reconnectTime(3000)
                    .data(payload)
            )
            emitter.complete()
        } catch (e: Exception) {
            logger.debug("Failed to replay final state for jobId={}: {}", jobId, e.message)
        }
        emitters[jobId]?.remove(emitter)
    }

    fun closeAll(jobId: String) {
        emitters.remove(jobId)?.forEach { emitter ->
            try { emitter.complete() } catch (_: Exception) { }
        }
    }

    private fun broadcast(jobId: String, eventName: String, payload: Map<String, Any?>) {
        val list = emitters[jobId] ?: return
        val dead = mutableListOf<SseEmitter>()
        for (emitter in list) {
            try {
                emitter.send(
                    SseEmitter.event()
                        .id(seq.incrementAndGet().toString())
                        .name(eventName)
                        .reconnectTime(3000)
                        .data(payload),
                )
            } catch (_: Exception) {
                dead += emitter
            }
        }
        list.removeAll(dead)
    }

    private fun remove(jobId: String, emitter: SseEmitter) {
        emitters[jobId]?.remove(emitter)
        logger.debug("Cleanup SSE client removed for jobId={}", jobId)
    }
}
