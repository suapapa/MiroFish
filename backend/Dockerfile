# MiroFish Backend - multi-stage build: builder (with compilers) -> slim runtime
# Runtime uses gunicorn and runs as a non-root user
# Note: build context is the repo root (backend code references root locales/ via ../../../locales)

# 1) Dependency build stage (includes gcc etc. to compile source-built packages like psutil)
FROM python:3.11-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=0

# Build tools required by packages that compile from source
RUN apt-get update \
  && apt-get install -y --no-install-recommends gcc python3-dev \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency manifests first to leverage build cache
COPY backend/pyproject.toml backend/uv.lock ./

# Install runtime dependencies only (no dev), creating /app/.venv (CPU torch by default)
RUN uv sync --frozen --no-dev --no-install-project

# Optional GPU support: defaults to cpu; pass cu121/cu124/cu126 etc. to use the matching CUDA torch
# e.g. docker compose build --build-arg TORCH_VARIANT=cu124
# Note: at runtime you still need the host NVIDIA driver + nvidia-container-toolkit, started with --gpus all
ARG TORCH_VARIANT=cpu
RUN if [ "$TORCH_VARIANT" != "cpu" ]; then \
      echo "Installing CUDA torch (${TORCH_VARIANT}) ..." && \
      uv pip install --python /app/.venv \
        --index-url "https://download.pytorch.org/whl/${TORCH_VARIANT}" torch ; \
    fi

# 2) Runtime stage (slim, no compilers)
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    FLASK_DEBUG=false \
    PATH="/app/.venv/bin:$PATH"

# Keep the same directory layout as the source: /app/backend + /app/locales
WORKDIR /app/backend

# Copy the prebuilt virtual environment from the builder
COPY --from=builder /app/.venv /app/.venv

# Copy backend source and shared locales (locale.py references ../../../locales)
COPY backend/ /app/backend/
COPY locales/ /app/locales/

# Create a non-root user and prepare the upload directory
RUN useradd --create-home --uid 10001 app \
  && mkdir -p /app/backend/uploads \
  && chown -R app:app /app
USER app

EXPOSE 5001

# Start the WSGI app via gunicorn (the module-level app object exposed by run.py)
CMD ["sh", "-c", "gunicorn run:app \
  --bind 0.0.0.0:5001 \
  --workers ${GUNICORN_WORKERS:-4} \
  --threads ${GUNICORN_THREADS:-2} \
  --timeout ${GUNICORN_TIMEOUT:-600} \
  --access-logfile - --error-logfile -"]
