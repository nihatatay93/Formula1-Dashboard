FROM python:3.13.13-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /workspace

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app ./app
COPY scripts ./scripts
COPY alembic.ini ./
COPY alembic ./alembic

# Run as an unprivileged user. The mount points are created and owned here
# rather than at runtime, because Docker seeds an *empty* named volume from the
# image directory it covers, ownership included — so a fresh deployment gets
# writable volumes with no start-up chown and no root in the container.
#
# A volume that already holds data is left alone by Docker and keeps the
# ownership it was created with, so an existing installation cannot write to it
# after this change. scripts/prepare-existing-volumes.sh fixes that in place.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home app \
    && mkdir -p /cache/fastf1 /live-sessions /live-auth \
    && chown -R app:app /cache /live-sessions /live-auth /workspace

USER app

EXPOSE 8000

CMD ["/opt/venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
