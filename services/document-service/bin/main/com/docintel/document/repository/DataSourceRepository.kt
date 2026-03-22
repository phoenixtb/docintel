package com.docintel.document.repository

import com.docintel.document.entity.DataSource
import org.springframework.data.jpa.repository.JpaRepository
import org.springframework.stereotype.Repository
import java.util.UUID

@Repository
interface DataSourceRepository : JpaRepository<DataSource, UUID> {

    fun findByIdAndTenantId(id: UUID, tenantId: String): DataSource?

    fun findByTenantId(tenantId: String): List<DataSource>
}
