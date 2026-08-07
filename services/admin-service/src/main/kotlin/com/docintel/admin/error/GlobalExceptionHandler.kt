package com.docintel.admin.error

import org.slf4j.LoggerFactory
import org.springframework.http.HttpStatus
import org.springframework.http.ResponseEntity
import org.springframework.web.bind.annotation.ExceptionHandler
import org.springframework.web.bind.annotation.RestControllerAdvice

/**
 * Standardizes every uncaught exception into the {"error": {code, message, request_id}}
 * envelope, replacing Spring Boot's default whitebox /error body
 * ({"timestamp","status","error","path"}).
 */
@RestControllerAdvice
class GlobalExceptionHandler {

    private val log = LoggerFactory.getLogger(javaClass)

    @ExceptionHandler(NoSuchElementException::class)
    fun handleNotFound(e: NoSuchElementException): ResponseEntity<ErrorEnvelope> =
        ResponseEntity.status(HttpStatus.NOT_FOUND)
            .body(errorEnvelope("NOT_FOUND", e.message ?: "Resource not found."))

    @ExceptionHandler(Exception::class)
    fun handleUnexpected(e: Exception): ResponseEntity<ErrorEnvelope> {
        log.error("Unhandled exception", e)
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
            .body(errorEnvelope("INTERNAL_ERROR", "Internal server error."))
    }
}
