import time
import uuid

from app.core.config import get_settings
from app.infrastructure.database import SessionLocal
from app.services.cases import reap_expired_cases
from app.services.redaction import install_log_redaction


def run_reaper() -> None:
    install_log_redaction()
    settings = get_settings()
    while True:
        with SessionLocal() as session:
            reap_expired_cases(
                session,
                max_attempts=settings.analysis_max_attempts,
                correlation_id=str(uuid.uuid4()),
            )
        time.sleep(max(settings.analysis_lease_seconds // 2, 15))


if __name__ == "__main__":
    run_reaper()
