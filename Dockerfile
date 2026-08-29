# Dockerfile for dbus-event-log
FROM python:3.14-slim@sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install system dependencies for pydbus/D-Bus
RUN apt-get update && apt-get install -y --no-install-recommends \
    dbus \
    libdbus-1-3 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY config.yaml.example ./config.yaml

# Install package
RUN pip install --no-cache-dir -e .

# Create non-root user
RUN useradd -m -u 1000 appuser && \
    mkdir -p /var/lib/dbus-event-log && \
    chown -R appuser:appuser /var/lib/dbus-event-log /app

USER appuser

# Default command
ENTRYPOINT ["dbus-event-log"]
CMD ["monitor"]