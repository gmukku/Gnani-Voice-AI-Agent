# Multi-stage: wheels are built once, then copied into a slim runtime so the
# final image carries no compiler toolchain.
#
# Pinned to 3.13 rather than 3.14: the version floors in requirements.txt exist
# because pydantic-core has no cp314 wheel below 2.12, and those same floors
# work on 3.13 with wheels universally available. Local development runs 3.14.
FROM python:3.13-slim-bookworm AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /wheels
COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt


FROM python:3.13-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

# curl is needed by the container healthcheck below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=build /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

# Templates and static assets are resolved relative to the working directory,
# so they must sit alongside the package.
COPY app ./app
COPY templates ./templates
COPY static ./static
COPY gnani ./gnani
COPY tests ./tests
COPY scripts ./scripts

# Run unprivileged. The data directory is created here so the JSON fallback
# backend works even when no volume is mounted.
RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
