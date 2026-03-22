package com.docintel.admin.tenant

object TenantContextHolder {
    private val ctx = ThreadLocal<TenantContext>()

    fun set(context: TenantContext) = ctx.set(context)

    fun get(): TenantContext = ctx.get() ?: TenantContext("default", "tenant_user", "")

    fun getTenantId(): String = get().tenantId

    fun getUserRole(): String = get().userRole

    fun clear() = ctx.remove()
}
