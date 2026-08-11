FROM python:3.14@sha256:3a9d2dd3f18e5c7a9d8de7b3659418a4ab848ccd409fb9e91ef9d7a6a3520ba7

# ── System dependencies ──────────────────────────────────────────────
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    git=1:2.47.3-0+deb13u1 \
    g++=4:14.2.0-1 \
    cargo=1.85.0+dfsg3-1 \
    ffmpeg=7:7.1.5-0+deb13u1 \
    mediainfo=25.04-1 \
    rustc=1.85.0+dfsg3-1 \
    nano=8.4-1+deb13u1 \
    ca-certificates=20250419 \
    curl=8.14.1-2+deb13u4 \
    gosu=1.17-3+b4 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* && \
    update-ca-certificates

# ── Python environment ──────────────────────────────────────────────
# Ensure Python output is sent straight to the container logs (no buffering)
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# ── Application setup ────────────────────────────────────────────────
WORKDIR /Upload-Assistant

# Copy DVD MediaInfo download script and run it
# This downloads specialized MediaInfo binaries for DVD processing with language support
COPY bin/__init__.py bin/get_dvd_mediainfo_docker.py bin/download_integrity.py bin/
RUN python3 -m bin.get_dvd_mediainfo_docker

# Copy the rest of the application
COPY . .

# Preserve the built-in data/ directory outside the mount-point so that
# volume mounts over /Upload-Assistant/data/ don't hide critical files
# (__init__.py, version.py, example-config.py, templates/).
# At runtime the entrypoint restores missing files from this copy.
RUN rm -rf /Upload-Assistant/defaults \
    && mkdir -p /Upload-Assistant/defaults \
    && cp -a data /Upload-Assistant/defaults/ \
    && find /Upload-Assistant/defaults/ -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null \
    && chmod 1777 /Upload-Assistant/data

# Download only the required mkbrr binary (requires full repo for src imports)
RUN python3 -c "from bin.get_mkbrr import MkbrrBinaryManager; MkbrrBinaryManager.download_mkbrr_for_docker()"

# Download bdinfo binary for the container architecture using the docker helper
RUN python3 bin/get_bdinfo_docker.py

# Ensure downloaded binaries are executable
RUN find bin/mkbrr -name "mkbrr" -print0 | xargs -0 chmod +x && \
    find bin/bdinfo -name "bdinfo" -print0 | xargs -0 chmod +x

# ── Permissions ──────────────────────────────────────────────────────
# Give UID 1000 ownership (runtime binary updates need chmod) and let
# any other UID (e.g. Unraid 99:100) read/execute.
RUN chown -R 1000:1000 /Upload-Assistant/bin/mkbrr \
    && chown -R 1000:1000 /Upload-Assistant/bin/MI \
    && chown -R 1000:1000 /Upload-Assistant/bin/bdinfo \
    && chmod -R o+rX /Upload-Assistant/bin/mkbrr \
    && chmod -R o+rX /Upload-Assistant/bin/MI \
    && chmod -R o+rX /Upload-Assistant/bin/bdinfo

# Dynamic HDR tools are downloaded on demand by the application.
RUN mkdir -p /Upload-Assistant/bin/dovi_tool /Upload-Assistant/bin/hdr10plus_tool \
    && chown -R 1000:1000 /Upload-Assistant/bin/dovi_tool /Upload-Assistant/bin/hdr10plus_tool \
    && chmod 1777 /Upload-Assistant/bin/dovi_tool /Upload-Assistant/bin/hdr10plus_tool

# Nyuu and 7-Zip are also downloaded on demand. Keep only these dedicated
# install directories writable for arbitrary container UIDs.
RUN mkdir -p /Upload-Assistant/bin/nyuu /Upload-Assistant/bin/7z /Upload-Assistant/bin/par2 /Upload-Assistant/bin/pesto /Upload-Assistant/bin/zentag \
    && chown -R 1000:1000 /Upload-Assistant/bin/nyuu /Upload-Assistant/bin/7z /Upload-Assistant/bin/par2 /Upload-Assistant/bin/pesto /Upload-Assistant/bin/zentag \
    && chmod 1777 /Upload-Assistant/bin/nyuu /Upload-Assistant/bin/7z /Upload-Assistant/bin/par2 /Upload-Assistant/bin/pesto /Upload-Assistant/bin/zentag

# Create tmp directory; world-writable so any UID can use it
RUN mkdir -p /Upload-Assistant/tmp && chmod 1777 /Upload-Assistant/tmp
ENV TMPDIR=/Upload-Assistant/tmp

# ── Runtime metadata ─────────────────────────────────────────────────
# Document the WebUI port (informational only; does not publish the port)
EXPOSE 5000

# Let Docker send SIGTERM for graceful shutdown (Python handles it in upload.py)
STOPSIGNAL SIGTERM

# Health check for WebUI mode — ignored when running CLI
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://localhost:5000/api/health || exit 1

# ── Entrypoint ───────────────────────────────────────────────────────
# The entrypoint script handles directory permissions and optional
# privilege-drop via PUID/PGID environment variables.
# Pass arguments via CMD or `docker run ... <args>`.
#   WebUI : docker run ... image --webui 0.0.0.0:5000
#   CLI   : docker run ... image /data/content --trackers BHD
COPY scripts/docker-entrypoint.sh /usr/local/bin/
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
    && chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# Default: show help when no arguments are provided
CMD ["-h"]
