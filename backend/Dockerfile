# CiteThreads backend — production image.
#
# WeasyPrint is the only dependency that needs system libraries
# (libpango / libcairo / libgdk-pixbuf for PDF export). Everything
# else is a pure-Python wheel.
#
# Build:    docker build -t citethreads-backend backend/
# Run:      docker run --rm -p 8000:8000 -v $(pwd)/data:/app/data citethreads-backend
FROM python:3.11-slim AS base

# ---------------------------------------------------------------------------
# System dependencies
# ---------------------------------------------------------------------------
# weasyprint pulls these in at runtime; installing them up front keeps
# the image deterministic and avoids a surprise "missing libpango" on
# the first PDF export.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        # weasyprint runtime
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        # common build tools (kept in the final image so wheels that
        # build C extensions work without a separate builder stage)
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Python environment
# ---------------------------------------------------------------------------
WORKDIR /app

# Install Python deps in their own layer so source changes don't
# invalidate the cache.
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
# Application code
# ---------------------------------------------------------------------------
COPY app ./app

# Pre-create the data directory the app writes to. ``ProjectStorage``
# will create it on first request, but having it here makes bind
# mounts and read-only root filesystems easier to reason about.
RUN mkdir -p /app/data/projects

# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
# Run as a non-root user. The base image ships with a `nonroot` UID
# (65532) that the upstream python images standardized on.
RUN useradd --create-home --shell /bin/bash --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

EXPOSE 8000

# ``--proxy-headers`` so the app sees the original client IP behind a
# load balancer; ``--no-server-header`` removes a fingerprinting
# header that some operators prefer to suppress.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--proxy-headers", \
     "--no-server-header"]
