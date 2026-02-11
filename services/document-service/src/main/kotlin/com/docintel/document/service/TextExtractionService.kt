package com.docintel.document.service

import org.apache.tika.Tika
import org.apache.tika.metadata.Metadata
import org.apache.tika.metadata.TikaCoreProperties
import org.apache.tika.parser.AutoDetectParser
import org.apache.tika.sax.BodyContentHandler
import org.springframework.stereotype.Service
import java.io.InputStream

data class ExtractionResult(
    val text: String,
    val metadata: Map<String, String>,
    val contentType: String?
)

@Service
class TextExtractionService {
    
    private val tika = Tika()
    private val parser = AutoDetectParser()

    /**
     * Extract text from a document using Apache Tika.
     * Supports PDF, DOCX, TXT, MD, HTML, and more.
     */
    fun extractText(inputStream: InputStream, filename: String): ExtractionResult {
        val metadata = Metadata()
        metadata.set(TikaCoreProperties.RESOURCE_NAME_KEY, filename)
        
        // Use -1 for unlimited content length
        val handler = BodyContentHandler(-1)
        
        parser.parse(inputStream, handler, metadata)
        
        val extractedMetadata = mutableMapOf<String, String>()
        metadata.names().forEach { name ->
            metadata.get(name)?.let { value ->
                extractedMetadata[name] = value
            }
        }
        
        return ExtractionResult(
            text = handler.toString().trim(),
            metadata = extractedMetadata,
            contentType = metadata.get("Content-Type")
        )
    }

    /**
     * Detect content type without full parsing.
     */
    fun detectContentType(inputStream: InputStream): String {
        return tika.detect(inputStream)
    }
}
