# Seedbox / Linux Install

This guide covers installing Upload Assistant on a Linux box or seedbox where you do not have root access.

## What this installer does

The bundled installer script:

1. Installs `pyenv` if needed.
2. Installs Python `3.14.0` by default.
3. Downloads and checksum-verifies the pinned `uv` release.
4. Uses the current checkout if you pass `--ua-dir`, or clones/updates Upload Assistant in `~/tools/ua` by default.
5. Synchronizes `.venv` from `pyproject.toml` and the frozen `uv.lock`.
6. Creates `run-ua.sh` for easier execution.

## Quick start

From a Linux shell:

```bash
git clone https://github.com/wastaken7/Upload-Assistant.git
cd Upload-Assistant
chmod +x scripts/install-seedbox.sh
./scripts/install-seedbox.sh --ua-dir "$PWD"
```

If you just want the installer to create or update a separate checkout in `~/tools/ua`, omit `--ua-dir "$PWD"`.

## Options

```text
--ua-dir PATH           Installation directory (default: ~/tools/ua)
--python VERSION        Python version for pyenv (default: 3.14.0)
--skip-pyenv-install    Fail instead of installing pyenv automatically
--force-update          Recreate .venv and synchronize the lockfile again
-h, --help              Show this help
```

## Requirements

These commands should already exist on the seedbox:

```bash
bash --version
git --version
curl --version
tar --version
sha256sum --version
```

To build Python with `pyenv`, many providers also need common build tooling already installed, such as:

```bash
gcc --version
make --version
```

If your provider compiled the host without the required development libraries, Python modules such as `_sqlite3` may still be unavailable. In that case, the fix is on the provider side, not in Upload Assistant.

## Running Upload Assistant

After installation:

```bash
cd /path/to/your/ua/checkout
./run-ua.sh "/path/to/content" --trackers yourtracker
```

If you prefer the raw environment:

```bash
cd /path/to/your/ua/checkout
source .venv/bin/activate
python upload.py "/path/to/content" --trackers yourtracker
```

## Updating

Run the installer again:

```bash
./scripts/install-seedbox.sh
```

Or update manually:

```bash
cd /path/to/your/ua/checkout
git pull --ff-only
uv sync --frozen --no-dev --no-install-project
```

## Notes about `local_path` / `remote_path`

`local_path` and `remote_path` are only path-mapping settings for torrent client integration.

They do not install UA remotely and they do not move Upload Assistant execution to another machine. To run the heavy work on a remote file-hosting box, install and invoke the CLI on that machine directly.
