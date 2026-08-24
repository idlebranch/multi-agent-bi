# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.8.15 AS uv

FROM python:3.12-slim

COPY --from=uv /uv /uvx /usr/local/bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}"

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY --chown=app:app api.py main.py ./
COPY --chown=app:app src ./src
COPY --chown=app:app static ./static
COPY --chown=app:app policies ./policies
COPY --chown=app:app data/olist_semantic_model.json ./data/olist_semantic_model.json
COPY --chown=app:app scripts/load_olist_postgres.py scripts/olist_source.py ./scripts/
COPY --chown=app:app postgres ./postgres

USER app:app

EXPOSE 8000

CMD ["uvicorn", "api:api", "--host", "0.0.0.0", "--port", "8000"]
