from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.enums import CaseStatus
from app.domain.models import ComplianceCase


def claim_next_case(session: Session) -> ComplianceCase | None:
    case_id = session.scalar(
        select(ComplianceCase.id)
        .where(ComplianceCase.status == CaseStatus.QUEUED)
        .order_by(ComplianceCase.created_at)
        .limit(1)
    )
    if case_id is None:
        return None
    claimed = session.execute(
        update(ComplianceCase)
        .where(
            ComplianceCase.id == case_id,
            ComplianceCase.status == CaseStatus.QUEUED,
        )
        .values(status=CaseStatus.ANALYZING, started_at=datetime.now(UTC))
    )
    session.commit()
    if claimed.rowcount != 1:
        return None
    return session.get(ComplianceCase, case_id)
