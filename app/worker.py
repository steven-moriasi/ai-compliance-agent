import time
import uuid
from pathlib import Path

from app.core.config import get_settings
from app.core.metrics import ANALYSES, ANALYSIS_LATENCY
from app.infrastructure.database import SessionLocal
from app.services.analysis import AnalysisService
from app.services.cases import claim_next_case
from app.services.classifier import load_cfr_prior
from app.services.provider_factory import build_provider
from app.services.redaction import install_log_redaction


def run_worker() -> None:
    install_log_redaction()
    settings = get_settings()
    provider = build_provider(settings)
    cfr_prior = load_cfr_prior(Path(settings.cfr_prior_path))
    worker_id = f"analysis-worker-{uuid.uuid4()}"
    while True:
        with SessionLocal() as session:
            case = claim_next_case(
                session,
                worker_id=worker_id,
                lease_seconds=settings.analysis_lease_seconds,
            )
            if case is None:
                time.sleep(1)
                continue
            analyzed = AnalysisService(session, settings, provider, cfr_prior=cfr_prior).analyze(
                case,
                correlation_id=str(uuid.uuid4()),
                worker_id=worker_id,
            )
            ANALYSES.labels(status=analyzed.status.value, provider=provider.name).inc()
            if analyzed.latency_ms is not None:
                ANALYSIS_LATENCY.labels(provider=provider.name).observe(
                    analyzed.latency_ms / 1000
                )


if __name__ == "__main__":
    run_worker()
