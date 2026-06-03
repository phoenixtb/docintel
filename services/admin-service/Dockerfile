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
# sharing=locked serializes access to the shared cache so parallel service
# builds don't fight over Gradle's journal lock (they share one cache, deps
# download once). vfs.watch=false avoids Gradle's file-watcher, which is flaky
# on BuildKit cache mounts and under cross-arch (amd64-on-arm64) emulation.
RUN --mount=type=cache,target=/root/.gradle,sharing=locked \
    ./gradlew dependencies --no-daemon -Dorg.gradle.vfs.watch=false || true

COPY src ./src
RUN --mount=type=cache,target=/root/.gradle,sharing=locked \
    ./gradlew bootJar --no-daemon -Dorg.gradle.vfs.watch=false

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
