#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$repo_dir"
if [[ -f .env ]]; then set -a; source .env; set +a; fi
python_bin="${PYTHON_BIN:-$repo_dir/.venv/bin/python}"
if [[ ! -x "$python_bin" ]]; then python_bin="${PYTHON_BIN:-python3}"; fi
exec "$python_bin" -m phoenix.cli our_method "$@"
