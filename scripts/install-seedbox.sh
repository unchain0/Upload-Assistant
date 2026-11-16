#!/usr/bin/env bash
set -euo pipefail

PYTHON_VERSION="3.14.0"
UV_VERSION="0.11.32"
UA_DIR="${HOME}/tools/ua"
SKIP_PYENV_INSTALL=0
FORCE_UPDATE=0
PYENV_ROOT_DEFAULT="${HOME}/.pyenv"
PYENV_GIT_REF="v2.6.7"
PYENV_REPO_URL="https://github.com/pyenv/pyenv.git"

usage() {
    cat <<'EOF'
Usage: install-seedbox.sh [options]

Install or update Upload Assistant on a Linux box without requiring root.

Options:
  --ua-dir PATH           Installation directory (default: ~/tools/ua)
  --python VERSION        Python version for pyenv (default: 3.14.0)
  --skip-pyenv-install    Fail instead of installing pyenv automatically
  --force-update          Recreate .venv and synchronize the lockfile again
  -h, --help              Show this help
EOF
}

log() { printf '==> %s\n' "$1"; }
fail() { printf 'Error: %s\n' "$1" >&2; exit 1; }
require_command() { command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"; }

append_pyenv_init() {
    local rc_file="$1"
    [ -f "$rc_file" ] || touch "$rc_file"
    if ! grep -q 'PYENV_ROOT' "$rc_file"; then
        cat >>"$rc_file" <<'EOF'

export PYENV_ROOT="$HOME/.pyenv"
export PATH="$HOME/.local/bin:$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"
EOF
    fi
}

setup_pyenv_env() {
    export PYENV_ROOT="$PYENV_ROOT_DEFAULT"
    export PATH="$HOME/.local/bin:$PYENV_ROOT/bin:$PATH"
    eval "$(pyenv init -)"
}

install_pyenv_if_needed() {
    if command -v pyenv >/dev/null 2>&1; then
        setup_pyenv_env
        return
    fi
    [ "$SKIP_PYENV_INSTALL" -eq 0 ] || fail "pyenv is not installed and --skip-pyenv-install was requested"
    require_command git
    log "Installing pyenv ${PYENV_GIT_REF}"
    if [ -e "$PYENV_ROOT_DEFAULT" ] && [ ! -d "$PYENV_ROOT_DEFAULT/.git" ]; then
        fail "Refusing to install pyenv because $PYENV_ROOT_DEFAULT exists and is not a git checkout"
    fi
    git clone --branch "$PYENV_GIT_REF" --depth 1 "$PYENV_REPO_URL" "$PYENV_ROOT_DEFAULT"
    setup_pyenv_env
    append_pyenv_init "$HOME/.bashrc"
    append_pyenv_init "$HOME/.profile"
}

install_uv() {
    export PATH="$HOME/.local/bin:$PATH"
    if command -v uv >/dev/null 2>&1 && uv --version | grep -q "uv ${UV_VERSION}$"; then
        return
    fi

    require_command curl
    require_command tar
    require_command sha256sum

    local target checksum archive temp_dir
    case "$(uname -m)" in
        x86_64|amd64)
            target="x86_64-unknown-linux-gnu"
            checksum="aab924fd522efd06f1c5f3b93a243864fc453132c94b2dc49f1371b528a4b967"
            ;;
        aarch64|arm64)
            target="aarch64-unknown-linux-gnu"
            checksum="4d4fa08d95b06642e5800df6a22bd71455f23f988269e18da2847971d8c0bf31"
            ;;
        *) fail "Unsupported architecture for uv: $(uname -m)" ;;
    esac

    temp_dir="$(mktemp -d)"
    trap 'rm -rf "$temp_dir"' RETURN
    archive="$temp_dir/uv.tar.gz"
    log "Downloading uv ${UV_VERSION}"
    curl --fail --location --silent --show-error \
        "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-${target}.tar.gz" \
        --output "$archive"
    printf '%s  %s\n' "$checksum" "$archive" | sha256sum --check --status || fail "uv archive checksum mismatch"
    tar -xzf "$archive" -C "$temp_dir"
    mkdir -p "$HOME/.local/bin"
    install -m 0755 "$temp_dir/uv-${target}/uv" "$HOME/.local/bin/uv"
    install -m 0755 "$temp_dir/uv-${target}/uvx" "$HOME/.local/bin/uvx"
    rm -rf "$temp_dir"
    trap - RETURN
}

install_python_if_needed() {
    if ! pyenv versions --bare | grep -qx "$PYTHON_VERSION"; then
        log "Installing Python ${PYTHON_VERSION} via pyenv"
        pyenv install "$PYTHON_VERSION"
    fi
}

clone_or_update_repo() {
    mkdir -p "$(dirname "$UA_DIR")"
    if [ ! -d "$UA_DIR/.git" ]; then
        log "Cloning Upload Assistant into ${UA_DIR}"
        git clone https://github.com/wastaken7/Upload-Assistant.git "$UA_DIR"
    else
        log "Updating existing Upload Assistant checkout"
        git -C "$UA_DIR" pull --ff-only
    fi
}

sync_dependencies() {
    cd -- "$UA_DIR"
    log "Selecting Python ${PYTHON_VERSION}"
    pyenv local "$PYTHON_VERSION"
    if [ "$FORCE_UPDATE" -eq 1 ]; then
        rm -rf .venv
    fi
    log "Synchronizing the frozen uv lockfile"
    UV_PROJECT_ENVIRONMENT=.venv uv sync \
        --frozen \
        --no-dev \
        --no-install-project \
        --python "$(pyenv which python)"
}

write_runner() {
    cat >"$UA_DIR/run-ua.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
exec .venv/bin/python upload.py "$@"
EOF
    chmod +x "$UA_DIR/run-ua.sh"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --ua-dir) [ "$#" -ge 2 ] || fail "--ua-dir requires a path"; UA_DIR="$2"; shift 2 ;;
        --python) [ "$#" -ge 2 ] || fail "--python requires a version"; PYTHON_VERSION="$2"; shift 2 ;;
        --skip-pyenv-install) SKIP_PYENV_INSTALL=1; shift ;;
        --force-update) FORCE_UPDATE=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) fail "Unknown option: $1" ;;
    esac
done

require_command git
require_command bash
install_pyenv_if_needed
require_command pyenv
install_uv
install_python_if_needed
clone_or_update_repo
sync_dependencies
write_runner

cat <<EOF

Installation complete.

Location:
  ${UA_DIR}

Configure:
  ${UA_DIR}/.venv/bin/python ${UA_DIR}/config-generator.py

Run:
  ${UA_DIR}/run-ua.sh "/path/to/content" --trackers yourtracker
EOF
