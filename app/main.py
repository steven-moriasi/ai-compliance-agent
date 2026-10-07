from fastapi import FastAPI

from app.api.routes import cases, catalog, demo, health, search
from app.services.redaction import install_log_redaction

install_log_redaction()

app = FastAPI(title="AI Compliance Agent", version="0.1.0")
app.include_router(health.router)
app.include_router(demo.router)
app.include_router(catalog.router)
app.include_router(cases.router)
app.include_router(search.router)
