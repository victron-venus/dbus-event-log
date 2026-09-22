# Dockerfile for dbus-event-log
FROM python:3.14-slim@sha256:caaf356f40667c496d405780745b9ac25771c189a51dfcc42430d531ea09f8a2

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Build PyGObject for the image Python and retain the GLib/Gio runtime typelibs.
RUN apt-get update && apt-get install -y --no-install-recommends \
    dbus \
    libdbus-1-3 \
    libglib2.0-0 \
    gir1.2-glib-2.0 \
    libgirepository-2.0-dev \
    gcc \
    libcairo2-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY config.yaml.example ./config.yaml

# Install the real D-Bus monitoring bindings and fail the build if imports break.
RUN pip install --no-cache-dir ".[monitor]" && \
    python -c "import pydbus; from gi.repository import GLib, Gio"

# Create non-root user
RUN useradd -m -u 1000 appuser && \
    mkdir -p /var/lib/dbus-event-log && \
    chown -R appuser:appuser /var/lib/dbus-event-log /app

USER appuser

# Default command
ENTRYPOINT ["dbus-event-log"]
CMD ["monitor"]
