# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# Dockerfile for dbus-event-log
FROM python:3.14-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d

ARG SOURCE_DATE_EPOCH=0

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY docker/debian.sources /etc/apt/sources.list.d/debian.sources

# Build PyGObject for the image Python and retain the GLib/Gio runtime typelibs.
RUN test "$(. /etc/os-release && echo "$VERSION_CODENAME")" = trixie && \
    apt-get update && PYTHONHASHSEED=0 apt-get install -y --no-install-recommends \
    dbus \
    libdbus-1-3 \
    libglib2.0-0 \
    gir1.2-glib-2.0 \
    libgirepository-2.0-dev \
    gcc \
    libcairo2-dev \
    pkg-config \
    && dpkg-query -W -f='${Package} ${Version} ${Architecture}\n' | LC_ALL=C sort > /usr/share/dbus-event-log-debian-packages.txt \
    && rm -rf /var/lib/apt/lists/* /var/log/apt/* /var/log/dpkg.log /var/log/alternatives.log \
    /var/cache/ldconfig/aux-cache /var/lib/dbus/machine-id

# Copy project files
COPY pyproject.toml README.md LICENSE requirements-docker*.txt ./
COPY src/ ./src/
COPY config.yaml.example ./config.yaml

# Build-only inputs honor caller compiler/linker settings for native dependencies.
# ARG values reach RUN but are not persisted in the runtime environment.
ARG CC
ARG CFLAGS
ARG CXX
ARG CXXFLAGS
ARG LDFLAGS

COPY docker/install-native.py /tmp/install-native.py

# Install the real D-Bus monitoring bindings and fail the build if imports break.
RUN pip install --no-cache-dir --no-compile --only-binary=:all: --require-hashes -r requirements-docker-build.txt && \
    PYTHONHASHSEED=0 python /tmp/install-native.py && \
    pip install --no-cache-dir --no-compile --only-binary=:all: --no-binary=dbus-event-log --no-index --no-deps --no-build-isolation . && \
    python -c "import pydbus; from gi.repository import GLib, Gio" && \
    rm /tmp/install-native.py

# Create non-root user
RUN useradd -m -u 1000 appuser && \
    chage --lastday "$((SOURCE_DATE_EPOCH / 86400))" appuser && \
    rm -f /etc/passwd- /etc/group- /etc/shadow- /etc/gshadow- && \
    mkdir -p /var/lib/dbus-event-log && \
    chown -R appuser:appuser /var/lib/dbus-event-log /app

USER appuser

# Default command
ENTRYPOINT ["dbus-event-log"]
CMD ["monitor"]
