package com.docintel.document.filter

import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

object HmacUtils {
    private const val ALGORITHM = "HmacSHA256"

    /**
     * Compute HMAC-SHA256 of [message] using [secret] and return a hex string.
     * Mirrors the Python implementation in docintel_common.internal_auth.
     */
    fun compute(message: String, secret: String): String {
        val mac = Mac.getInstance(ALGORITHM)
        mac.init(SecretKeySpec(secret.toByteArray(Charsets.UTF_8), ALGORITHM))
        return mac.doFinal(message.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }
    }

    /**
     * Verify a token from the gateway:  HMAC(requestId:tenantId:userId, secret)
     * or from another service:          HMAC("":tenantId:"",            secret)
     *
     * We accept both forms so one filter handles both sources.
     */
    fun verify(
        token: String,
        requestId: String,
        tenantId: String,
        userId: String,
        secret: String,
    ): Boolean {
        val expected = compute("$requestId:$tenantId:$userId", secret)
        return constantTimeEquals(expected, token)
    }

    private fun constantTimeEquals(a: String, b: String): Boolean {
        if (a.length != b.length) return false
        var result = 0
        for (i in a.indices) result = result or (a[i].code xor b[i].code)
        return result == 0
    }
}
