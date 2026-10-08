import logging
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.metrics import ANALYSES, ANALYSIS_LATENCY
from app.infrastructure.database import SessionLocal, engine
from app.services.analysis import AnalysisService
from app.services.answers import AnswerService
from app.services.cases import claim_next_case, release_failed_claim
from app.services.classifier import CfrPrior, load_cfr_prior
from app.services.embeddings import Embedder, RetrievalUnavailable, sentence_transformer_embedder
from app.services.provider_factory import build_provider
from app.services.providers import AnswerProvider, ModelProvider
from app.services.questions import claim_next_question, release_failed_question
from app.services.redaction import install_log_redaction
from app.services.retrieval import resolve_retrieval_mode

logger = logging.getLogger("app.worker")

SEMANTIC_MODES = frozenset({"vector", "embedding", "hybrid"})


def load_worker_embedder(settings: Settings, dialect: str) -> Callable[[], Embedder] | None:
    """Load the embedding model before the first claim, so no case waits for it.

    Returns None when the configured mode does not use embeddings. When the model cannot be
    loaded, every case falls back to lexical search and records why, without retrying the load.
    """
    mode = resolve_retrieval_mode(settings.retrieval_mode, dialect)
    if mode not in SEMANTIC_MODES:
        return None
    started = time.perf_counter()
    try:
        embedder = sentence_transformer_embedder(
            settings.embedding_model,
            settings.embedding_revision,
        )
    except RetrievalUnavailable as exc:
        reason = str(exc)
        logger.warning("%s retrieval unavailable, cases use lexical search: %s", mode, reason)

        def unavailable() -> Embedder:
            raise RetrievalUnavailable(reason)

        return unavailable
    logger.info(
        "Loaded %s for %s retrieval in %.1f s",
        settings.embedding_model,
        mode,
        time.perf_counter() - started,
    )
    return lambda: embedder


def process_next_case(
    session: Session,
    settings: Settings,
    provider: ModelProvider,
    worker_id: str,
    *,
    cfr_prior: CfrPrior | None = None,
    embedder_loader: Callable[[], Embedder] | None = None,
) -> bool:
    """Claim and analyse one case. Returns False when nothing was queued.

    An unexpected error does not stop the worker. The case is handed back with the
    exception type recorded, and the next poll continues.
    """
    correlation_id = str(uuid.uuid4())
    case = claim_next_case(
        session,
        worker_id=worker_id,
        lease_seconds=settings.analysis_lease_seconds,
        correlation_id=correlation_id,
    )
    if case is None:
        return False
    case_id = case.id
    fencing_token = case.fencing_token
    try:
        analyzed = AnalysisService(
            session,
            settings,
            provider,
            cfr_prior=cfr_prior,
            embedder_loader=embedder_loader,
        ).analyze(case, correlation_id=correlation_id, worker_id=worker_id)
    except Exception as exc:
        logger.exception("Analysis of case %s stopped: %s", case_id, type(exc).__name__)
        ANALYSES.labels(status="error", provider=provider.name).inc()
        _release(
            session,
            settings,
            case_id=case_id,
            worker_id=worker_id,
            fencing_token=fencing_token,
            correlation_id=correlation_id,
            error_type=type(exc).__name__,
        )
        return True
    ANALYSES.labels(status=analyzed.status.value, provider=provider.name).inc()
    if analyzed.latency_ms is not None:
        ANALYSIS_LATENCY.labels(provider=provider.name).observe(analyzed.latency_ms / 1000)
    return True


def process_next_question(
    session: Session,
    settings: Settings,
    provider: AnswerProvider,
    worker_id: str,
    *,
    cfr_prior: CfrPrior | None = None,
    embedder_loader: Callable[[], Embedder] | None = None,
) -> bool:
    """Claim and answer one policy question. Returns False when nothing was queued."""
    correlation_id = str(uuid.uuid4())
    question = claim_next_question(
        session,
        worker_id=worker_id,
        lease_seconds=settings.analysis_lease_seconds,
        correlation_id=correlation_id,
    )
    if question is None:
        return False
    question_id = question.id
    fencing_token = question.fencing_token
    try:
        AnswerService(
            session,
            settings,
            provider,
            cfr_prior=cfr_prior,
            embedder_loader=embedder_loader,
        ).answer(question, correlation_id=correlation_id, worker_id=worker_id)
    except Exception as exc:
        logger.exception("Question %s stopped: %s", question_id, type(exc).__name__)
        try:
            release_failed_question(
                session,
                question_id=question_id,
                worker_id=worker_id,
                fencing_token=fencing_token,
                max_attempts=settings.analysis_max_attempts,
                correlation_id=correlation_id,
                error_type=type(exc).__name__,
            )
        except SQLAlchemyError:
            logger.exception("Question %s could not be released; the reaper will", question_id)
    return True


def run_worker() -> None:
    install_log_redaction()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    provider = build_provider(settings)
    cfr_prior = load_cfr_prior(Path(settings.cfr_prior_path))
    embedder_loader = load_worker_embedder(settings, engine.dialect.name)
    worker_id = f"analysis-worker-{uuid.uuid4()}"
    logger.info(
        "Worker %s is polling for cases and questions with the %s provider",
        worker_id,
        provider.name,
    )
    while True:
        with SessionLocal() as session:
            claimed = process_next_case(
                session,
                settings,
                provider,
                worker_id,
                cfr_prior=cfr_prior,
                embedder_loader=embedder_loader,
            ) or process_next_question(
                session,
                settings,
                provider,
                worker_id,
                cfr_prior=cfr_prior,
                embedder_loader=embedder_loader,
            )
        if not claimed:
            time.sleep(1)


def _release(
    session: Session,
    settings: Settings,
    *,
    case_id: str,
    worker_id: str,
    fencing_token: int,
    correlation_id: str,
    error_type: str,
) -> None:
    try:
        status = release_failed_claim(
            session,
            case_id=case_id,
            worker_id=worker_id,
            fencing_token=fencing_token,
            max_attempts=settings.analysis_max_attempts,
            correlation_id=correlation_id,
            error_type=error_type,
        )
    except SQLAlchemyError:
        logger.exception("Case %s could not be released; the reaper will requeue it", case_id)
        return
    if status is not None:
        logger.info("Case %s is %s after the error", case_id, status.value)


if __name__ == "__main__":
    run_worker()
