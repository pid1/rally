FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src/ src/

RUN uv sync --no-dev --frozen

FROM python:3.14-slim

WORKDIR /app
COPY --from=builder /app/.venv .venv
COPY --from=builder /app/src src/
COPY static/ static/
COPY templates/ templates/
COPY migrate_add_due_date.py migrate_add_due_date.py
COPY run_migrations.py run_migrations.py
COPY entrypoint.sh entrypoint.sh

RUN chmod +x entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src:$PYTHONPATH" \
    RALLY_ENV=production \
    RALLY_DB_PATH="/data/rally.db"

EXPOSE 8000

VOLUME /data
VOLUME /output

CMD ["./entrypoint.sh"]
