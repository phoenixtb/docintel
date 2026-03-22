package com.docintel.document.sse

import org.slf4j.LoggerFactory
import org.springframework.context.event.EventListener
import org.springframework.stereotype.Component
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicLong

/**
 * Holds active SSE connections keyed by tenantId.
 *
 * Listens for [DocumentStatusEvent] (fired by [DocumentService] at every lifecycle
 * transition) and broadcasts to all emitters registered for that tenant.
 *
 * Event ID: monotonically increasing sequence so the browser's Last-Event-ID header
 * works on reconnect.  Server-side replay is not implemented; instead the SSE endpoint
 * sends a full in-flight snapshot on every (re)connect, making replay unnecessary.
 *
 * reconnectTime (retry:) is sent with every event so the browser knows to wait 3s
 * before reconnecting if the connection drops.  No per-connection scheduled task
 * is used — the single shared @EventListener handles all tenants.
 */
@Component
class SseEmitterRegistry {

    private val logger = LoggerFactory.getLogger(SseEmitterRegistry::class.java)
    private val emitters = ConcurrentHashMap<String, CopyOnWriteArrayList<SseEmitter>>()
    private val eventSequence = AtomicLong(0)

    fun register(tenantId: String): SseEmitter {
        val emitter = SseEmitter(Long.MAX_VALUE)
        emitters.getOrPut(tenantId) { CopyOnWriteArrayList() }.add(emitter)

        val cleanup = Runnable { remove(tenantId, emitter) }
        emitter.onCompletion(cleanup)
        emitter.onTimeout(cleanup)
        emitter.onError { cleanup.run() }

        logger.debug("SSE client registered for tenant={}", tenantId)
        return emitter
    }

    /**
     * Send a pre-built snapshot event (current_state) directly to a specific emitter.
     * Called by the controller immediately after [register] to give the new client the
     * current in-flight document list before live events start flowing.
     */
    fun sendSnapshot(emitter: SseEmitter, payloads: List<Map<String, Any?>>) {
        payloads.forEach { payload ->
            try {
                emitter.send(
                    SseEmitter.event()
                        .id(eventSequence.incrementAndGet().toString())
                        .name("current_state")
                        .reconnectTime(3000)
                        .data(payload)
                )
            } catch (_: Exception) { /* emitter already gone */ }
        }
    }

    @EventListener
    fun onDocumentStatusEvent(event: DocumentStatusEvent) {
        val list = emitters[event.tenantId] ?: return
        val eventId = eventSequence.incrementAndGet().toString()
        val payload = mapOf(
            "documentId"   to event.documentId,
            "status"       to event.status,
            "stage"        to event.stage,
            "filename"     to event.filename,
            "chunkCount"   to event.chunkCount,
            "errorMessage" to event.errorMessage,
        )
        val dead = mutableListOf<SseEmitter>()
        for (emitter in list) {
            try {
                emitter.send(
                    SseEmitter.event()
                        .id(eventId)
                        .name("document_status")
                        .reconnectTime(3000)
                        .data(payload)
                )
            } catch (_: Exception) {
                dead += emitter
            }
        }
        list.removeAll(dead)
    }

    private fun remove(tenantId: String, emitter: SseEmitter) {
        emitters[tenantId]?.remove(emitter)
        logger.debug("SSE client removed for tenant={}", tenantId)
    }
}
