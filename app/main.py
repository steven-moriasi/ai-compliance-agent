from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import cases, catalog, health
from app.domain.models import Base
from app.infrastructure.database import engine


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="AI Compliance Agent", version="0.1.0", lifespan=lifespan)
app.include_router(health.router)
app.include_router(catalog.router)
app.include_router(cases.router)
