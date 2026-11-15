#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
export PRIMITIVE_CONFIG="${1:-${PRIMITIVE_CONFIG:-config.json}}"
exec "$project_root/.venv/bin/primitive-vla" desktop --factory primitive_vla.application:create_workbench
