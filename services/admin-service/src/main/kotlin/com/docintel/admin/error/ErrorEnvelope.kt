package com.docintel.admin.error

import jakarta.servlet.http.HttpServletResponse
import org.slf4j.MDC

/**
 * Unified error response shape across all DocIntel services (Kotlin + Python):
 *
 *   {"error": {"code": "...", "message": "...", "request_id": "..."}}
 *
 * `request_id` comes from MDC (populated by RequestContextFilter for every
 * request, servlet-filter or controller alike) so it lines up with the
 * X-Request-Id response header and backend logs.
 */
data class ErrorBody(val code: String, val message: String, val requestId: String)
data class ErrorEnvelope(val error: ErrorBody)

fun errorEnvelope(code: String, message: String): ErrorEnvelope =
    ErrorEnvelope(ErrorBody(code, message, MDC.get("requestId") ?: "-"))

/**
 * For servlet Filters, which run before the DispatcherServlet and therefore
 * can't be caught by @RestControllerAdvice — they must write the response
 * body directly.
 */
fun writeErrorEnvelope(
    response: HttpServletResponse,
    status: Int,
    code: String,
    message: String,
    mapper: com.fasterxml.jackson.databind.ObjectMapper,
) {
    response.status = status
    response.contentType = "application/json"
    response.writer.write(mapper.writeValueAsString(errorEnvelope(code, message)))
}
