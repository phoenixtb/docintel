package com.docintel.document.tenant

import kotlinx.coroutines.ThreadContextElement
import kotlin.coroutines.CoroutineContext

/**
 * Propagates [TenantContextHolder] (ThreadLocal) across coroutine suspension/resumption.
 *
 * Without this, any suspend call on [Dispatchers.Default] or [Dispatchers.IO] can resume
 * on a different thread where the ThreadLocal is blank. [TenantAwareDataSource] reads
 * TenantContextHolder to set `app.current_tenant` on every JDBC connection checkout, so a
 * missing tenant causes PostgreSQL RLS to see `current_tenant = 'default'` and return
 * zero rows for every query.
 *
 * Usage: launch(TenantCoroutineContext(tenantId)) { ... }
 *
 * Restores the previous value on suspension so it is safe to nest inside another
 * coroutine that already carries a different tenant.
 */
class TenantCoroutineContext(private val tenantId: String) : ThreadContextElement<TenantContext?> {

    companion object Key : CoroutineContext.Key<TenantCoroutineContext>

    override val key: CoroutineContext.Key<*> get() = Key

    override fun updateThreadContext(context: CoroutineContext): TenantContext? {
        val prev = TenantContextHolder.get()
        TenantContextHolder.setTenantId(tenantId)
        return prev
    }

    override fun restoreThreadContext(context: CoroutineContext, oldState: TenantContext?) {
        if (oldState != null) TenantContextHolder.set(oldState)
        else TenantContextHolder.clear()
    }
}
