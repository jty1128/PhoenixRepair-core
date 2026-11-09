#!/usr/bin/env bash
# DeepSeek V4.1 Flash: localization + P0, using current GLM experiment settings.
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$repo_dir/our_method.sh" \
  --config "$repo_dir/configs/models/deepseek-v4.1-flash/our_method.yaml" "$@"
