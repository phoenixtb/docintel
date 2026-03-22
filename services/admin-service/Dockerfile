# Admin Service Dockerfile
# Kotlin/Spring Boot

FROM eclipse-temurin:21-jdk-alpine AS build

WORKDIR /app

# gradle-wrapper.jar is committed — no curl needed.
COPY build.gradle.kts settings.gradle.kts ./
COPY gradle ./gradle
COPY gradlew ./
RUN chmod +x gradlew

# Resolve deps with a persistent Gradle cache (like ~/.gradle on the host).
# The cache mount is shared across all builds; deps are only downloaded once,
# even when build.gradle.kts changes — same behaviour as a local Gradle install.
RUN --mount=type=cache,target=/root/.gradle \
    ./gradlew dependencies --no-daemon || true

COPY src ./src
RUN --mount=type=cache,target=/root/.gradle \
    ./gradlew bootJar --no-daemon

FROM eclipse-temurin:21-jre-alpine

WORKDIR /app

COPY --from=build /app/build/libs/*.jar app.jar

COPY --chmod=755 docker-entrypoint.sh /docker-entrypoint.sh

RUN addgroup -S appgroup && adduser -S appuser -G appgroup
USER appuser

EXPOSE 8082

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD wget -q --spider http://localhost:8082/actuator/health || exit 1

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["java", "-jar", "app.jar"]
