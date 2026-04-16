package com.docintel.document.repository

import com.docintel.document.entity.DeletionTask
import com.docintel.document.entity.DeletionTaskStatus
import org.springframework.data.domain.Pageable
import org.springframework.data.jpa.repository.JpaRepository
import org.springframework.stereotype.Repository
import java.util.UUID

@Repository
interface DeletionTaskRepository : JpaRepository<DeletionTask, UUID> {

    fun findByTaskStatus(status: DeletionTaskStatus, pageable: Pageable): List<DeletionTask>

    fun countByTaskStatus(status: DeletionTaskStatus): Long
}
