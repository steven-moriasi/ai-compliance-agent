import time

import httpx

from app.core.config import get_settings
from app.infrastructure.database import SessionLocal
from app.services.notifications import claim_notification, deliver_notification


def run_notifier() -> None:
    settings = get_settings()
    if settings.notification_webhook_url is None or settings.notification_webhook_secret is None:
        raise ValueError("Notification webhook URL and secret are required")
    with httpx.Client(timeout=15) as client:
        while True:
            with SessionLocal() as session:
                notification = claim_notification(
                    session,
                    lease_seconds=settings.notification_lease_seconds,
                )
                if notification is None:
                    time.sleep(1)
                    continue
                deliver_notification(
                    session,
                    notification,
                    webhook_url=settings.notification_webhook_url,
                    webhook_secret=settings.notification_webhook_secret.get_secret_value(),
                    max_attempts=settings.notification_max_attempts,
                    client=client,
                )


if __name__ == "__main__":
    run_notifier()
