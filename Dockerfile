FROM ghcr.io/astral-sh/uv:0.11.7 AS uv

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="/app" \
    PATH="/app/.venv/bin:$PATH"

COPY --from=uv /uv /uvx /bin/

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts
RUN uv sync --frozen --no-dev && chown -R app:app /app

USER app

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
