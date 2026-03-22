FROM python:3.12-alpine

WORKDIR /app
COPY main.py .
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# No extra dependencies — stdlib only
RUN adduser -D -u 1001 actions
USER actions

EXPOSE 8090
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD wget -qO- http://127.0.0.1:8090/health || exit 1

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["python", "main.py"]
