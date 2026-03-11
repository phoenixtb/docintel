# Admin Service Dockerfile
# Kotlin/Spring Boot

FROM eclipse-temurin:21-jdk-alpine AS build

WORKDIR /app

# Install curl for downloading gradle wrapper
RUN apk add --no-cache curl

# Copy Gradle files
COPY build.gradle.kts settings.gradle.kts ./
COPY gradle ./gradle

# Download gradle wrapper jar if not present
RUN mkdir -p gradle/wrapper && \
    if [ ! -f gradle/wrapper/gradle-wrapper.jar ]; then \
        curl -fsSL -o gradle/wrapper/gradle-wrapper.jar \
        https://github.com/gradle/gradle/raw/v8.11.1/gradle/wrapper/gradle-wrapper.jar; \
    fi

# Copy gradlew script and make executable
COPY gradlew ./
RUN chmod +x gradlew

# Download dependencies
RUN ./gradlew dependencies --no-daemon || true

# Copy source and build
COPY src ./src
RUN ./gradlew bootJar --no-daemon

# Runtime image
FROM eclipse-temurin:21-jre-alpine

WORKDIR /app

COPY --from=build /app/build/libs/*.jar app.jar

RUN addgroup -S appgroup && adduser -S appuser -G appgroup
USER appuser

EXPOSE 8082

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD wget -q --spider http://localhost:8082/actuator/health || exit 1

ENTRYPOINT ["java", "-jar", "app.jar"]
