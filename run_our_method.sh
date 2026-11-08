#!/usr/bin/env bash
# Compatibility spelling for the original three-entry request.
set -euo pipefail
exec bash "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/our_method.sh" "$@"
