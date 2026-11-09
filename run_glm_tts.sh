#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$repo_dir/run_tts.sh" --config "$repo_dir/configs/models/glm-5.2/tts.yaml" "$@"
