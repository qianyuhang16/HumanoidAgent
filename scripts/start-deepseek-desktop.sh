#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
export PYTHONPATH="$project_root/workspace/deps/RPent${PYTHONPATH:+:$PYTHONPATH}"
exec "$project_root/.venv/bin/primitive-vla" desktop \
  --factory primitive_vla.interfaces.web.deepseek:create_workbench
