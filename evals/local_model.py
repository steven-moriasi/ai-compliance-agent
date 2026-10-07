"""Measure one evaluation file against a local model.

The default evaluation command stays on the deterministic fixture provider.
This module records whatever the configured local model actually returns.
It does not write a report when the server cannot be reached.
"""

import json
from pathlib import Path

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.domain.models import Base
from app.services.analysis import AnalysisService
from app.services.cases import claim_next_case
from app.services.provider_factory import build_provider
from app.services.providers import ModelProvider
from evals.run import EvaluationCase, _create_case, load_evaluation_set


def measure_case(evaluation: EvaluationCase, provider: ModelProvider) -> dict[str, object]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with factory() as session:
            _create_case(session, evaluation)
            claimed = claim_next_case(session, worker_id="local-model", lease_seconds=120)
            if claimed is None:
                raise RuntimeError(f"Evaluation case {evaluation.id} could not be claimed")
            analyzed = AnalysisService(
                session,
                Settings(confidence_threshold=0.7, model_provider=provider.name),
                provider,
            ).analyze(
                claimed,
                correlation_id=f"local-model-{evaluation.id}",
                worker_id="local-model",
            )
            return {
                "case_id": evaluation.id,
                "provider": provider.name,
                "model": provider.model,
                "status": analyzed.status.value,
                "outcome": None if analyzed.outcome is None else analyzed.outcome.value,
                "validation_errors": list(analyzed.validation_errors),
                "latency_ms": analyzed.latency_ms,
            }
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def local_server_reachable(base_url: str, timeout_seconds: float = 3) -> bool:
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    try:
        response = httpx.get(f"{root}/api/tags", timeout=timeout_seconds)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def main() -> int:
    settings = Settings(model_provider="ollama")
    if not local_server_reachable(settings.local_model_base_url):
        print("local model server is not reachable; no evaluation report was written")
        return 1
    provider = build_provider(settings)
    evaluation_set = load_evaluation_set(Path("evals/cases/v1.json"))
    report = {
        "provider": provider.name,
        "model": provider.model,
        "cases": [measure_case(case, provider) for case in evaluation_set.cases],
    }
    destination = Path("evals/reports/local_model.json")
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {destination} for {provider.model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
