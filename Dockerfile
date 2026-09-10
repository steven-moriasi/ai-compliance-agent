FROM python:3.12.11-slim-bookworm AS builder

WORKDIR /build
COPY pyproject.toml README.md ./
COPY app ./app
RUN python -m pip install --no-cache-dir build==1.2.2.post1 \
    && python -m build --wheel

FROM python:3.12.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --system compliance \
    && useradd --system --gid compliance --create-home compliance

WORKDIR /app
COPY --from=builder /build/dist/*.whl /tmp/
RUN python -m pip install --no-cache-dir /tmp/*.whl \
    && rm -rf /tmp/*.whl
COPY alembic.ini ./
COPY alembic ./alembic

USER compliance
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
