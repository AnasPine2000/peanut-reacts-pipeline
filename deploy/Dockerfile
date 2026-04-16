# Peanut Reacts Pipeline - production container
# Builds a self-contained image with FFmpeg, yt-dlp, and the full Python stack.
# Designed for Hetzner CPX31 / Oracle Always Free / any Linux VPS.

FROM python:3.12-slim-bookworm

LABEL maintainer="peanut-reacts"
LABEL description="Autonomous YouTube content pipeline - reaction + ambient + learning"

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    curl \
    ca-certificates \
    fonts-dejavu-core \
    fontconfig \
    libsndfile1 \
    libsox-fmt-all \
    sqlite3 \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Set timezone (override at runtime with -e TZ=...)
ENV TZ=Europe/Amsterdam

# Python environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Copy requirements first for better Docker cache
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy the source code
COPY src /app/src
COPY scripts /app/scripts
COPY channels.yaml /app/channels.yaml
COPY pyproject.toml /app/pyproject.toml

# Install peanut_reacts package itself
RUN pip install --no-cache-dir -e /app

# Persistent volumes for data, tokens, output
VOLUME ["/data", "/output", "/tokens", "/secrets"]

# Default entry: start the scheduler daemon
ENV PYTHONPATH=/app/src
WORKDIR /app

# Healthcheck: scheduler should be running
HEALTHCHECK --interval=5m --timeout=10s --start-period=30s \
    CMD pgrep -f "peanut_reacts.scheduler.runner" || exit 1

CMD ["python", "-m", "peanut_reacts.scheduler.runner"]
