package com.docintel.document.tenant

object TenantContextHolder {
    private val ctx = ThreadLocal<TenantContext>()

    fun set(context: TenantContext) = ctx.set(context)

    fun get(): TenantContext = ctx.get() ?: TenantContext("default", "tenant_user", "")

    fun getTenantId(): String = get().tenantId

    fun getUserRole(): String = get().userRole

    fun setTenantId(tenantId: String) = ctx.set(get().copy(tenantId = tenantId))

    fun setUserRole(role: String) = ctx.set(get().copy(userRole = role))

    fun clear() = ctx.remove()
}
