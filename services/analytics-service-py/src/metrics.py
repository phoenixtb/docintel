"""
Prometheus metrics for the analytics.query Redis Streams consumer (A7).
"""

from prometheus_client import Counter, Gauge

ANALYTICS_BATCH_FLUSH_TOTAL = Counter(
    "analytics_batch_flush_total",
    "Number of times the query-event buffer was flushed to ClickHouse",
    ["topic"],
)

ANALYTICS_EVENTS_FLUSHED_TOTAL = Counter(
    "analytics_events_flushed_total",
    "Number of query telemetry events successfully inserted into ClickHouse",
    ["topic"],
)

ANALYTICS_STREAM_LAG = Gauge(
    "analytics_stream_lag",
    "Consumer group lag — entries in the stream not yet delivered to this group",
    ["topic", "group"],
)
