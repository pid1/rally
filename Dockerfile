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
COPY migrations/ migrations/
COPY entrypoint.sh entrypoint.sh

RUN chmod +x entrypoint.sh

# PYTHONPATH is not set in the base image, so appending to it left a trailing
# colon — and an empty path entry means the *current directory*, which put
# /app on sys.path on top of the /app/src we actually wanted. Nothing imports
# anything from /app (the migration runner finds its siblings through its own
# script directory), so this only ever widened the import surface. PATH is a
# different case and still appends: the base image does define it.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    RALLY_ENV=production \
    RALLY_DB_PATH="/data/rally.db"

EXPOSE 8000

# trivy DS-0002 / checkov CKV_DOCKER_3: do not run as root. The mount
# points are created and chowned *before* the VOLUME lines, because
# changes made to a declared volume path later in the build are discarded.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data /output \
    && chown -R 10001 /app /data /output

VOLUME /data
VOLUME /output

USER 10001

CMD ["./entrypoint.sh"]
