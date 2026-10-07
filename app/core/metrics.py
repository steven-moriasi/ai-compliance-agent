from prometheus_client import Counter, Histogram

ANALYSES = Counter(
    "compliance_analysis_total",
    "Compliance analysis outcomes",
    ("status", "provider"),
)
ANALYSIS_LATENCY = Histogram(
    "compliance_analysis_latency_seconds",
    "Model analysis latency",
    ("provider",),
)
RETRIEVAL_FALLBACKS = Counter(
    "compliance_retrieval_fallback_total",
    "Times vector or hybrid retrieval fell back to lexical search",
)
REVIEWS = Counter(
    "compliance_review_total",
    "Human review decisions",
    ("decision",),
)
