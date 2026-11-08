"""Repair a prepared SWE-ReX runtime for musl images and literal PTY input."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


def repair(runtime):
    python = runtime / 'python'
    libraries = set()
    for binary in [python / 'bin/python3', *python.glob('lib/python*/**/*.so')]:
        output = subprocess.run(['ldd', str(binary)], capture_output=True, text=True, check=False).stdout
        for name in re.findall(r'(/[^\s()]+)', output):
            if name.startswith(('/lib/', '/lib64/', '/usr/lib/')) and Path(name).is_file():
                libraries.add(Path(name))
    bundle = python / 'compat-glibc'
    bundle.mkdir(exist_ok=True)
    for library in libraries:
        target = bundle / library.name
        temporary = target.with_suffix(target.suffix + '.tmp')
        shutil.copy2(library, temporary)
        temporary.replace(target)
    if not (bundle / 'ld-linux-x86-64.so.2').is_file():
        raise RuntimeError('The x86_64 glibc loader was not found')
    local = next(python.glob('lib/python*/site-packages/swerex/runtime/local.py'))
    source = local.read_text()
    if '--norc --noediting' not in source:
        if '"/usr/bin/env bash --noprofile --norc",' not in source:
            raise RuntimeError('Unrecognized SWE-ReX shell launcher; review before patching')
        source = source.replace('"/usr/bin/env bash --noprofile --norc",',
                                '"/usr/bin/env bash --noprofile --norc --noediting",')
    if 'cmds += ["stty -icanon", "set +o emacs; set +o vi"]' not in source:
        source = source.replace('cmds += self._get_reset_commands()',
                                'cmds += ["stty -icanon", "set +o emacs; set +o vi"]\n        cmds += self._get_reset_commands()', 1)
    local.write_text(source)
    wrapper = runtime / 'swerex-remote'
    temporary = wrapper.with_suffix('.tmp')
    shutil.copy2(Path(__file__).resolve().parents[1] / 'runtime/swerex-remote', temporary)
    temporary.chmod(0o755)
    temporary.replace(wrapper)
    return {'wrapper_sha256': hashlib.sha256(wrapper.read_bytes()).hexdigest(),
            'swerex_local_sha256': hashlib.sha256(local.read_bytes()).hexdigest(),
            'glibc_compat': sorted(p.name for p in libraries),
            'shell_line_editing': False, 'shell_canonical_input': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runtime', type=Path)
    args = parser.parse_args()
    print(json.dumps(repair(args.runtime), indent=2))
