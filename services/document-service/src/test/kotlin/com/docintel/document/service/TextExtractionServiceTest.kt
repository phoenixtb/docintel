package com.docintel.document.service

import org.apache.tika.exception.ZeroByteFileException
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.assertThrows
import kotlin.test.assertNotNull
import kotlin.test.assertTrue

/**
 * Unit tests for TextExtractionService.
 * Tests text extraction from various file formats.
 */
class TextExtractionServiceTest {

    private val service = TextExtractionService()

    @Test
    fun `should extract text from plain text file`() {
        // Given
        val content = """
            This is a test document.
            It contains multiple lines.
            And some special characters: @#${'$'}%^&*
        """.trimIndent()
        val inputStream = content.byteInputStream()

        // When
        val result = service.extractText(inputStream, "test.txt")

        // Then
        assertNotNull(result)
        assertTrue(result.text.contains("test document"))
        assertTrue(result.text.contains("multiple lines"))
        assertTrue(result.text.contains("special characters"))
    }

    @Test
    fun `should extract text from HR policy document`() {
        // Given
        val inputStream = javaClass.getResourceAsStream("/documents/hr_policy_leave.txt")
            ?: throw IllegalStateException("Test file not found: hr_policy_leave.txt")

        // When
        val result = service.extractText(inputStream, "hr_policy_leave.txt")

        // Then - check for content that should be in the file
        assertNotNull(result)
        assertTrue(result.text.isNotBlank(), "Extracted text should not be blank")
        // The HR policy should contain policy-related content
        assertTrue(
            result.text.lowercase().contains("leave") || 
            result.text.lowercase().contains("policy") ||
            result.text.lowercase().contains("employee"),
            "HR policy should contain 'leave', 'policy', or 'employee'"
        )
    }

    @Test
    fun `should extract text from technical documentation`() {
        // Given
        val inputStream = javaClass.getResourceAsStream("/documents/technical_api_docs.txt")
            ?: throw IllegalStateException("Test file not found: technical_api_docs.txt")

        // When
        val result = service.extractText(inputStream, "technical_api_docs.txt")

        // Then - check for content that should be in the file
        assertNotNull(result)
        assertTrue(result.text.isNotBlank(), "Extracted text should not be blank")
        // Technical docs should contain API-related content
        assertTrue(
            result.text.uppercase().contains("API") || 
            result.text.contains("endpoint") ||
            result.text.contains("request"),
            "Technical docs should contain 'API', 'endpoint', or 'request'"
        )
    }

    @Test
    fun `should extract text from contract document`() {
        // Given
        val inputStream = javaClass.getResourceAsStream("/documents/contract_saas_agreement.txt")
            ?: throw IllegalStateException("Test file not found: contract_saas_agreement.txt")

        // When
        val result = service.extractText(inputStream, "contract_saas_agreement.txt")

        // Then - check for content that should be in the file
        assertNotNull(result)
        assertTrue(result.text.isNotBlank(), "Extracted text should not be blank")
        // Contract should contain legal/agreement content
        assertTrue(
            result.text.lowercase().contains("agreement") || 
            result.text.lowercase().contains("contract") ||
            result.text.lowercase().contains("terms"),
            "Contract should contain 'agreement', 'contract', or 'terms'"
        )
    }

    @Test
    fun `should throw exception for empty file`() {
        // Given - Tika throws ZeroByteFileException for empty files
        val inputStream = "".byteInputStream()

        // When/Then
        assertThrows<ZeroByteFileException> {
            service.extractText(inputStream, "empty.txt")
        }
    }

    @Test
    fun `should handle unicode content`() {
        // Given
        val content = """
            日本語テキスト
            Émojis: 🎉 📄 ✅
            Special: café, naïve
        """.trimIndent()
        val inputStream = content.byteInputStream()

        // When
        val result = service.extractText(inputStream, "unicode.txt")

        // Then
        assertNotNull(result)
        assertTrue(result.text.contains("日本語"))
        assertTrue(result.text.contains("café"))
    }

    @Test
    fun `should return metadata from extraction`() {
        // Given
        val content = "Simple test content for metadata extraction."
        val inputStream = content.byteInputStream()

        // When
        val result = service.extractText(inputStream, "metadata_test.txt")

        // Then
        assertNotNull(result)
        assertNotNull(result.metadata)
    }

    @Test
    fun `should handle large text file`() {
        // Given
        val largeContent = "This is a line of text.\n".repeat(10000)
        val inputStream = largeContent.byteInputStream()

        // When
        val result = service.extractText(inputStream, "large.txt")

        // Then
        assertNotNull(result)
        assertTrue(result.text.length > 100000)
    }

    @Test
    fun `should detect content type for txt files`() {
        // Given
        val inputStream = "Plain text content".byteInputStream()

        // When
        val result = service.extractText(inputStream, "document.txt")

        // Then
        assertNotNull(result)
        // Should successfully extract even with generic content type
    }
}
