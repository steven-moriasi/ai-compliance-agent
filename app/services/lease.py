"""Keep a claimed case's or question's lease alive during a long retrieval or model call.

The fencing token does not change. Finalisation still has to present the token
from the original claim, and a renewal after expiry does not resurrect it.
"""

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.services.cases import renew_case_lease

RenewLease = Callable[[Session, str, str, int, int], bool]


@contextmanager
def lease_heartbeat(
    bind: Engine | Connection | None,
    *,
    record_id: str,
    worker_id: str,
    fencing_token: int,
    lease_seconds: int,
    interval_seconds: float | None = None,
    renew: RenewLease = renew_case_lease,
) -> Iterator[None]:
    """Renew until the caller finishes. A missing bind means there is nothing to extend."""
    if bind is None:
        yield
        return
    interval = interval_seconds if interval_seconds is not None else max(lease_seconds / 3, 1)
    stopper = threading.Event()
    worker = threading.Thread(
        target=_renew_until_stopped,
        kwargs={
            "bind": bind,
            "record_id": record_id,
            "renew": renew,
            "worker_id": worker_id,
            "fencing_token": fencing_token,
            "lease_seconds": lease_seconds,
            "interval": interval,
            "stopper": stopper,
        },
        name="lease-heartbeat",
        daemon=True,
    )
    worker.start()
    try:
        yield
    finally:
        stopper.set()
        worker.join(timeout=max(interval, 1))


def _renew_until_stopped(
    *,
    bind: Engine | Connection,
    record_id: str,
    renew: RenewLease,
    worker_id: str,
    fencing_token: int,
    lease_seconds: int,
    interval: float,
    stopper: threading.Event,
) -> None:
    factory = sessionmaker(bind=bind, expire_on_commit=False)
    while not stopper.wait(interval):
        _renew_once(factory, renew, record_id, worker_id, fencing_token, lease_seconds)


def _renew_once(
    factory: sessionmaker[Session],
    renew: RenewLease,
    record_id: str,
    worker_id: str,
    fencing_token: int,
    lease_seconds: int,
) -> None:
    """A locked SQLite connection is the other session's transaction. Retry once."""
    for _attempt in (1, 2):
        try:
            with factory() as session:
                renew(session, record_id, worker_id, fencing_token, lease_seconds)
            return
        except OperationalError:
            continue
