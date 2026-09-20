#!/usr/bin/env bash
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
git -C "$root" config core.hooksPath .githooks
printf 'Git hooks enabled: core.hooksPath=.githooks\n'
