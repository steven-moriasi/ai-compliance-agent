from sqlalchemy.orm import Session

from app.domain.models import AuditEvent
from app.domain.types import JsonObject


def append_audit_event(
    session: Session,
    *,
    event_type: str,
    actor_id: str,
    correlation_id: str,
    case_id: str | None = None,
    details: JsonObject | None = None,
) -> None:
    session.add(
        AuditEvent(
            event_type=event_type,
            actor_id=actor_id,
            case_id=case_id,
            correlation_id=correlation_id,
            details=details or {},
        )
    )
