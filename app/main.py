from fastapi import FastAPI

from app.api.routes import cases, catalog, health, search

app = FastAPI(title="AI Compliance Agent", version="0.1.0")
app.include_router(health.router)
app.include_router(catalog.router)
app.include_router(cases.router)
app.include_router(search.router)
