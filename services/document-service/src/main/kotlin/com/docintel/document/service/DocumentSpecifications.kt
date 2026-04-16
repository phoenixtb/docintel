package com.docintel.document.service

import com.docintel.document.dto.CleanupFiltersRequest
import com.docintel.document.dto.UploadOrigin
import com.docintel.document.entity.Document
import com.docintel.document.entity.ProcessingStatus
import org.springframework.data.jpa.domain.Specification
import java.time.Instant
import java.util.UUID

/**
 * Composable JPA Specifications for Document queries.
 * All specs assume the caller has already scoped to a tenant via [forTenant].
 */
object DocumentSpecifications {

    fun forTenant(tenantId: String) = Specification<Document> { root, _, cb ->
        cb.equal(root.get<String>("tenantId"), tenantId)
    }

    fun withStatuses(statuses: List<ProcessingStatus>) = Specification<Document> { root, _, _ ->
        root.get<ProcessingStatus>("status").`in`(statuses)
    }

    fun createdAfter(instant: Instant) = Specification<Document> { root, _, cb ->
        cb.greaterThanOrEqualTo(root.get("createdAt"), instant)
    }

    fun createdBefore(instant: Instant) = Specification<Document> { root, _, cb ->
        cb.lessThanOrEqualTo(root.get("createdAt"), instant)
    }

    fun withContentType(prefix: String) = Specification<Document> { root, _, cb ->
        cb.like(root.get("contentType"), "$prefix%")
    }

    fun manualUpload() = Specification<Document> { root, _, cb ->
        cb.isNull(root.get<UUID?>("dataSourceId"))
    }

    fun fromAnyDataSource() = Specification<Document> { root, _, cb ->
        cb.isNotNull(root.get<UUID>("dataSourceId"))
    }

    fun withDataSourceId(id: UUID) = Specification<Document> { root, _, cb ->
        cb.equal(root.get<UUID>("dataSourceId"), id)
    }

    fun withMetadataDomain(domain: String) = Specification<Document> { root, _, cb ->
        cb.equal(
            cb.function(
                "jsonb_extract_path_text", String::class.java,
                root.get<Any>("metadata"), cb.literal("domain"),
            ),
            domain,
        )
    }

    fun withMetadataSource(source: String) = Specification<Document> { root, _, cb ->
        cb.equal(
            cb.function(
                "jsonb_extract_path_text", String::class.java,
                root.get<Any>("metadata"), cb.literal("source"),
            ),
            source,
        )
    }

    /** Assemble all active filters into a single tenant-scoped Specification. */
    fun fromFilters(tenantId: String, filters: CleanupFiltersRequest): Specification<Document> {
        var spec = forTenant(tenantId)

        filters.statuses?.takeIf { it.isNotEmpty() }?.let { spec = spec.and(withStatuses(it)) }
        filters.createdAfter?.let { spec = spec.and(createdAfter(it)) }
        filters.createdBefore?.let { spec = spec.and(createdBefore(it)) }
        filters.domain?.takeIf { it.isNotBlank() }?.let { spec = spec.and(withMetadataDomain(it)) }
        filters.contentType?.takeIf { it.isNotBlank() }?.let { spec = spec.and(withContentType(it)) }
        filters.metadataSource?.takeIf { it.isNotBlank() }?.let { spec = spec.and(withMetadataSource(it)) }

        when {
            filters.dataSourceId != null -> spec = spec.and(withDataSourceId(filters.dataSourceId))
            filters.uploadOrigin == UploadOrigin.MANUAL -> spec = spec.and(manualUpload())
            filters.uploadOrigin == UploadOrigin.DATA_SOURCE -> spec = spec.and(fromAnyDataSource())
        }

        return spec
    }
}
