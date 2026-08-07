package com.docintel.admin.config

import io.swagger.v3.oas.models.Components
import io.swagger.v3.oas.models.OpenAPI
import io.swagger.v3.oas.models.info.Info
import io.swagger.v3.oas.models.security.SecurityScheme
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration

/**
 * Per-service OpenAPI metadata. Paths here are service-local (e.g. /admin/tenants);
 * scripts/generate-openapi.sh rewrites them to the public gateway surface
 * (e.g. /api/v1/admin/tenants) when merging into docs/api/openapi.json.
 */
@Configuration
class OpenApiConfig {

    @Bean
    fun adminServiceOpenApi(): OpenAPI =
        OpenAPI()
            .info(
                Info()
                    .title("DocIntel Admin Service")
                    .description("Tenant/user administration. Reached via the gateway at /api/v1/admin/**.")
                    .version("v1"),
            )
            .components(
                Components().addSecuritySchemes(
                    "bearerAuth",
                    SecurityScheme()
                        .type(SecurityScheme.Type.HTTP)
                        .scheme("bearer")
                        .bearerFormat("JWT"),
                ),
            )
}
