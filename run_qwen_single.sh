#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$repo_dir/run_single.sh" --config "$repo_dir/configs/models/qwen3.7-plus/single.yaml" "$@"
