#!/bin/sh
set -eu

# Prepare writable runtime state, optionally repair bind-mount ownership, then
# execute the CLI. The application checkout remains read-only at runtime.
TARGET_UID="${PUID:-1000}"
TARGET_GID="${PGID:-$TARGET_UID}"
STATE_DIR="${UA_DATA_DIR:-/state}"

prepare_state() {
    mkdir -p "$STATE_DIR" "$STATE_DIR/data" "$STATE_DIR/tmp"

    # Preserve the legacy config location expected by existing deployments,
    # without replacing any user-owned file.
    if [ ! -f "$STATE_DIR/data/config.py" ]; then
        if [ -f /Upload-Assistant/data/config.py ]; then
            cp /Upload-Assistant/data/config.py "$STATE_DIR/data/config.py"
        elif [ -f /Upload-Assistant/data/example_config.py ]; then
            cp /Upload-Assistant/data/example_config.py "$STATE_DIR/data/config.py"
        fi
    fi

    chmod 700 "$STATE_DIR" "$STATE_DIR/data" "$STATE_DIR/tmp" 2>/dev/null || true
    find "$STATE_DIR/data" -type f -exec chmod 600 {} + 2>/dev/null || true
}

if [ "$(id -u)" = "0" ]; then
    prepare_state

    for dir in \
        "$STATE_DIR" \
        /Upload-Assistant/bin/dovi_tool \
        /Upload-Assistant/bin/hdr10plus_tool \
        /Upload-Assistant/bin/nyuu \
        /Upload-Assistant/bin/7z \
        /Upload-Assistant/bin/par2 \
        /Upload-Assistant/bin/pesto \
        /Upload-Assistant/bin/zentag; do
        mkdir -p "$dir"
        if [ -n "$TARGET_UID" ]; then
            chown -R "$TARGET_UID:$TARGET_GID" "$dir" 2>/dev/null || true
        fi
    done

    if [ -n "$TARGET_UID" ] && [ "$TARGET_UID" != "0" ]; then
        exec gosu "$TARGET_UID:$TARGET_GID" python /Upload-Assistant/upload.py "$@"
    fi
else
    prepare_state
fi

exec python /Upload-Assistant/upload.py "$@"
