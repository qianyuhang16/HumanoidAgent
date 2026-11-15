#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
export PRIMITIVE_VLM_CONFIG="${1:-${PRIMITIVE_VLM_CONFIG:-configs/vlm.json}}"
export PRIMITIVE_RUNTIME_CONFIG="${2:-${PRIMITIVE_RUNTIME_CONFIG:-configs/runtime.json}}"
exec "$project_root/.venv/bin/primitive-vla" desktop --factory primitive_vla.application:create_workbench
