"""Prepare a relocatable tool runtime without changing a task's own interpreter."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

from phoenix.runtime_repair import repair


def validate(path):
    path = Path(path).resolve()
    for name in ("python/bin/python3", "swerex-remote"):
        if not (path / name).is_file():
            raise ValueError(f"Missing runtime file {name}; run python -m phoenix.runtime prepare --output PATH")
    return path


def prepare(output):
    output = Path(output).resolve()
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
        raise ValueError("The portable benchmark runtime currently supports Linux x86_64")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use a new empty runtime directory")
    output.mkdir(parents=True, exist_ok=True)
    try:
        import uv  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("Install runtime preparation support: python -m pip install uv") from exc
    with tempfile.TemporaryDirectory(prefix="phoenix-python-") as tmp:
        subprocess.run([sys.executable, "-m", "uv", "python", "install", "3.11", "--install-dir", tmp], check=True)
        matches = list(Path(tmp).glob("cpython-3.11*-linux-*/bin/python3"))
        if len(matches) != 1:
            raise RuntimeError("Expected one uv-managed Python 3.11 installation")
        shutil.copytree(matches[0].parent.parent, output / "python", symlinks=True)
    python = output / "python/bin/python3"
    subprocess.run([str(python), "-m", "ensurepip", "--upgrade"], check=True)
    subprocess.run([str(python), "-m", "pip", "install", "swe-rex==1.4.0", "tree-sitter==0.21.3", "tree-sitter-languages==1.10.2"], check=True)
    metadata = repair(output)
    (output / "runtime.json").write_text(json.dumps(metadata, indent=2) + "\n")
    validate(output)
    print("Runtime prepared: " + str(output))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "check"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.output)
    else:
        print(validate(args.output))


if __name__ == "__main__":
    main()
