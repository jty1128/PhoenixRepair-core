#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$repo_dir/our_method.sh" --config "$repo_dir/configs/models/qwen3.7-plus/our_method.yaml" "$@"
