package com.docintel.gateway.config

import org.springframework.beans.factory.annotation.Value
import org.springframework.cloud.gateway.route.RouteLocator
import org.springframework.cloud.gateway.route.builder.RouteLocatorBuilder
import org.springframework.context.annotation.Bean
import org.springframework.context.annotation.Configuration

/**
 * Programmatic route definitions.
 * 
 * Note: Routes are also defined in application.yml. 
 * This config is disabled to avoid duplicates - the YAML routes are sufficient.
 */
@Configuration
class GatewayConfig {
    // Routes are defined in application.yml / application-docker.yml
    // This class is kept for future programmatic route customization if needed
}
