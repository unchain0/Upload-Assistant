FROM ghcr.io/astral-sh/uv:0.11.32 AS uv

FROM python:3.14@sha256:3a9d2dd3f18e5c7a9d8de7b3659418a4ab848ccd409fb9e91ef9d7a6a3520ba7

# ── System dependencies ──────────────────────────────────────────────
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    git=1:2.47.3-0+deb13u1 \
    g++=4:14.2.0-1 \
    cargo=1.85.0+dfsg3-1 \
    ffmpeg=7:7.1.5-0+deb13u1 \
    rustc=1.85.0+dfsg3-1 \
    nano=8.4-1+deb13u1 \
    ca-certificates=20250419 \
    curl=8.14.1-2+deb13u4 \
    gosu=1.17-3+b4 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* && \
    update-ca-certificates

# ── Python environment ──────────────────────────────────────────────
COPY --from=uv /uv /uvx /usr/local/bin/
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV UV_PROJECT_ENVIRONMENT=/venv
ENV UV_LINK_MODE=copy
ENV PATH="/venv/bin:$PATH"

# ── Application setup ────────────────────────────────────────────────
WORKDIR /Upload-Assistant
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy the CLI application after dependency resolution for effective layer caching.
COPY . .

# Download the pinned official MediaInfo CLI used by the application.
RUN python3 -c "import asyncio; from src.integrations.runtime_tools.media_info_binary import MediaInfoBinaryManager; asyncio.run(MediaInfoBinaryManager.ensure_mediainfo_binary('/Upload-Assistant'))"

# Preserve the built-in data/ directory outside the mount-point so that
# volume mounts over /Upload-Assistant/data/ don't hide critical files
# (__init__.py, example-config.py, templates/).
# At runtime the app restores any missing files from this copy.
RUN rm -rf /Upload-Assistant/defaults \
    && mkdir -p /Upload-Assistant/defaults \
    && cp -a data /Upload-Assistant/defaults/ \
    && find /Upload-Assistant/defaults/ -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null \
    && cp -n /Upload-Assistant/data/example_config.py /Upload-Assistant/data/config.py \
    && find /Upload-Assistant/data -type d -exec chmod 0755 {} + \
    && find /Upload-Assistant/data -type f -exec chmod 0644 {} +

# Download only the required mkbrr binary (requires full repo for src imports)
RUN python3 -c "from src.integrations.runtime_tools.mkbrr import MkbrrBinaryManager; MkbrrBinaryManager.download_mkbrr_for_docker()"

# Download bdinfo binary for the container architecture using the docker helper
RUN python3 scripts/install_bdinfo_docker.py

# Ensure downloaded binaries are executable
RUN find bin/mkbrr -name "mkbrr" -print0 | xargs -0 chmod +x && \
    find bin/bdinfo -name "bdinfo" -print0 | xargs -0 chmod +x

# ── Permissions ──────────────────────────────────────────────────────
# Give UID 1000 ownership for the default runtime while keeping bundled
# executables immutable to unrelated UIDs. Arbitrary UIDs use a private cache.
RUN mkdir -p /Upload-Assistant/bin/MI \
    && chown -R 1000:1000 /Upload-Assistant/bin/mkbrr \
    && chown -R 1000:1000 /Upload-Assistant/bin/MI \
    && chown -R 1000:1000 /Upload-Assistant/bin/bdinfo \
    && find /Upload-Assistant/bin/mkbrr /Upload-Assistant/bin/MI /Upload-Assistant/bin/bdinfo -type d -exec chmod 0755 {} + \
    && chmod -R o+rX /Upload-Assistant/bin/mkbrr \
    && chmod -R o+rX /Upload-Assistant/bin/MI \
    && chmod -R o+rX /Upload-Assistant/bin/bdinfo

# Runtime tools are downloaded on demand. Their bundled roots are readable but
# not writable by unrelated UIDs; the application falls back to a private cache.
RUN mkdir -p /Upload-Assistant/bin/dovi_tool /Upload-Assistant/bin/hdr10plus_tool \
    && chown -R 1000:1000 /Upload-Assistant/bin/dovi_tool /Upload-Assistant/bin/hdr10plus_tool \
    && chmod 0755 /Upload-Assistant/bin/dovi_tool /Upload-Assistant/bin/hdr10plus_tool

# Nyuu, 7-Zip, PAR2, Pesto and Zentag are also downloaded on demand.
RUN mkdir -p /Upload-Assistant/bin/nyuu /Upload-Assistant/bin/7z /Upload-Assistant/bin/par2 /Upload-Assistant/bin/pesto /Upload-Assistant/bin/zentag \
    && chown -R 1000:1000 /Upload-Assistant/bin/nyuu /Upload-Assistant/bin/7z /Upload-Assistant/bin/par2 /Upload-Assistant/bin/pesto /Upload-Assistant/bin/zentag \
    && chmod 0755 /Upload-Assistant/bin/nyuu /Upload-Assistant/bin/7z /Upload-Assistant/bin/par2 /Upload-Assistant/bin/pesto /Upload-Assistant/bin/zentag

# All runtime state belongs outside the application checkout.  Mount /state to
# persist configuration, caches, and temporary release artifacts.
RUN mkdir -p /state && chmod 1777 /state
ENV UA_DATA_DIR=/state
ENV TMPDIR=/state/tmp
ENV MPLCONFIGDIR=/state/matplotlib
ENV XDG_CACHE_HOME=/state/cache

# ── Runtime metadata ─────────────────────────────────────────────────
# Let Docker send SIGTERM for graceful CLI shutdown.
STOPSIGNAL SIGTERM

# ── Entrypoint ───────────────────────────────────────────────────────
# The entrypoint script handles directory permissions and optional
# privilege-drop via PUID/PGID environment variables.
# Pass arguments via CMD or `docker run ... <args>`.
#   docker run ... image /data/content --trackers BHD
COPY scripts/docker-entrypoint.sh /usr/local/bin/
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
    && chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# Default: show help when no arguments are provided
CMD ["-h"]
