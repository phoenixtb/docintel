package com.docintel.admin.filter

import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

object HmacUtils {
    private const val ALGORITHM = "HmacSHA256"

    fun compute(message: String, secret: String): String {
        val mac = Mac.getInstance(ALGORITHM)
        mac.init(SecretKeySpec(secret.toByteArray(Charsets.UTF_8), ALGORITHM))
        return mac.doFinal(message.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }
    }

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
