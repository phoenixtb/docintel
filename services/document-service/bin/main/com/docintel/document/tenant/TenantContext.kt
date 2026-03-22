package com.docintel.document.tenant

data class TenantContext(
    val tenantId: String,
    val userRole: String,
    val userId: String,
)
