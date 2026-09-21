import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast

import httpx
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.domain.enums import NotificationStatus
from app.domain.models import ComplianceCase, NotificationOutbox


class _RowCountResult(Protocol):
    rowcount: int


def enqueue_review_notification(
    session: Session,
    case: ComplianceCase,
    decision: str,
    reviewer_id: str,
) -> NotificationOutbox:
    notification = NotificationOutbox(
        case_id=case.id,
        event_type="compliance_case_reviewed",
        payload={
            "case_id": case.id,
            "status": case.status.value,
            "decision": decision,
            "reviewer_id": reviewer_id,
        },
    )
    session.add(notification)
    return notification


def claim_notification(
    session: Session,
    lease_seconds: int,
) -> NotificationOutbox | None:
    now = datetime.now(UTC)
    candidate = (
        select(NotificationOutbox.id)
        .where(
            or_(
                (
                    (NotificationOutbox.status == NotificationStatus.PENDING)
                    & (NotificationOutbox.available_at <= now)
                ),
                (
                    (NotificationOutbox.status == NotificationStatus.DELIVERING)
                    & (NotificationOutbox.lease_expires_at <= now)
                ),
            )
        )
        .order_by(NotificationOutbox.created_at)
        .limit(1)
    )
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        candidate = candidate.with_for_update(skip_locked=True)
    notification_id = session.scalar(candidate)
    if notification_id is None:
        return None
    claimed = cast(
        _RowCountResult,
        session.execute(
            update(NotificationOutbox)
            .where(NotificationOutbox.id == notification_id)
            .values(
                status=NotificationStatus.DELIVERING,
                attempts=NotificationOutbox.attempts + 1,
                fencing_token=NotificationOutbox.fencing_token + 1,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
            )
            .execution_options(synchronize_session=False)
        )
    )
    session.commit()
    if claimed.rowcount != 1:
        return None
    return session.get(NotificationOutbox, notification_id, populate_existing=True)


def deliver_notification(
    session: Session,
    notification: NotificationOutbox,
    webhook_url: str,
    webhook_secret: str,
    max_attempts: int,
    client: httpx.Client,
) -> NotificationOutbox:
    body = json.dumps(notification.payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    try:
        response = client.post(
            webhook_url,
            content=body,
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": notification.id,
                "X-Compliance-Signature": f"sha256={signature}",
            },
        )
        response.raise_for_status()
    except httpx.HTTPError:
        if notification.attempts >= max_attempts:
            next_status = NotificationStatus.DEAD
            available_at = notification.available_at
        else:
            next_status = NotificationStatus.PENDING
            available_at = datetime.now(UTC) + timedelta(
                seconds=min(2**notification.attempts, 300)
            )
        values: dict[str, object] = {
            "status": next_status,
            "available_at": available_at,
            "last_error": "Webhook delivery failed",
            "lease_expires_at": None,
        }
    else:
        values = {
            "status": NotificationStatus.SENT,
            "delivered_at": datetime.now(UTC),
            "last_error": None,
            "lease_expires_at": None,
        }
    session.execute(
        update(NotificationOutbox)
        .where(
            NotificationOutbox.id == notification.id,
            NotificationOutbox.status == NotificationStatus.DELIVERING,
            NotificationOutbox.fencing_token == notification.fencing_token,
            NotificationOutbox.lease_expires_at > datetime.now(UTC),
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    session.commit()
    session.refresh(notification)
    return notification
