"""Prometheus metrics."""

from prometheus_client import Counter, Histogram, Info, generate_latest, CONTENT_TYPE_LATEST

app_info = Info("afm_app", "Africa Frontier Markets application info")

http_requests_total = Counter(
    "afm_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)
http_request_duration = Histogram(
    "afm_http_request_duration_seconds",
    "HTTP request duration",
    ["method", "endpoint"],
)

payments_processed = Counter(
    "afm_payments_processed_total",
    "Payments processed",
    ["psp", "status", "currency"],
)
trades_executed = Counter(
    "afm_trades_executed_total",
    "Trades executed",
    ["market", "side", "broker"],
)

revenue_split = Counter(
    "afm_revenue_split_total",
    "Revenue distribution",
    ["recipient_type"],
)


def get_metrics_response():
    return generate_latest()
