package com.docintel.document

import org.springframework.boot.autoconfigure.SpringBootApplication
import org.springframework.boot.runApplication
import org.springframework.scheduling.annotation.EnableScheduling

@SpringBootApplication
@EnableScheduling
class DocumentServiceApplication

fun main(args: Array<String>) {
    runApplication<DocumentServiceApplication>(*args)
}
