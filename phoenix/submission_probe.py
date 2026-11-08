"""Deterministic submission validation and test-only diff cleanup; no model review."""
import hashlib

import json

import os

from pathlib import Path

import re

import signal

import subprocess

import tempfile

import time

def git(root, *args, data=None):
    return subprocess.run(['git', *args], cwd=root, input=data, capture_output=True, timeout=30)

def checkout_state(root):
    diff = git(root, 'diff', '--binary', '--no-ext-diff', 'HEAD')
    names = git(root, 'ls-files', '--others', '--exclude-standard', '-z')
    if diff.returncode or names.returncode:
        raise ValueError('Cannot fingerprint the current checkout')
    extra = []
    for name in names.stdout.decode().split('\0'):
        if not name:
            continue
        path = Path(root) / name
        if path.is_symlink():
            value = ('symlink:' + os.readlink(path)).encode()
        else:
            value = path.read_bytes()
        extra.append((name, hashlib.sha256(value).hexdigest()))
    head = git(root, 'rev-parse', 'HEAD').stdout
    index = git(root, 'diff', '--cached', '--binary', '--no-ext-diff').stdout
    fingerprint = hashlib.sha256(head + diff.stdout + index + json.dumps(extra).encode()).hexdigest()
    return {'fingerprint': fingerprint, 'patch_sha256': hashlib.sha256(diff.stdout).hexdigest(),
            'untracked': extra}

def is_test_path(path):
    return any(x in ('tests', 'test', '__tests__') for x in Path(path).parts) or bool(
        re.search(r'(^|/)(test_[^/]+|[^/]+_test\.[^/]+)$|\.(test|spec)\.[^/]+$', path))

def inspect_patch(root, patch_path):
    patch = Path(patch_path).read_bytes()
    state = checkout_state(root)
    reverse = git(root, 'apply', '--reverse', '--check', '--binary', '-', data=patch)
    names = git(root, 'diff', '--name-only', '-z', 'HEAD').stdout.decode().split('\0')
    paths = [p for p in names if p]
    forbidden = [p for p in paths if is_test_path(p)]
    valid = bool(patch.strip() and paths and not forbidden and reverse.returncode == 0
                 and state['patch_sha256'] == hashlib.sha256(patch).hexdigest() and not state['untracked'])
    return {'valid': valid, 'patch_sha256': hashlib.sha256(patch).hexdigest(),
            'checkout_patch_sha256': state['patch_sha256'], 'changed_files': paths,
            'forbidden_test_paths': forbidden, 'reverse_apply_exit_code': reverse.returncode,
            'reverse_apply_output': reverse.stderr.decode(errors='replace'), 'untracked': state['untracked']}

def normalize_test_submission(root, patch_path):
    """Exclude test changes from a submission without changing production hunks.

    The caller archives the original patch first. Never clean unrelated files,
    guess at a mismatched patch, or change production code to satisfy a test.
    """
    check = inspect_patch(root, patch_path)
    forbidden = check['forbidden_test_paths']
    if not forbidden or check['reverse_apply_exit_code'] or check['untracked'] or check['patch_sha256'] != check['checkout_patch_sha256']:
        raise ValueError('Cleanup requires an exact current patch whose only mechanical failure is test-file changes')
    production = [p for p in check['changed_files'] if p not in forbidden]
    if not production:
        raise ValueError('Refusing to normalize a test-only patch into an empty submission')
    root = Path(root).resolve()
    for name in forbidden:
        p = root / name
        if Path(name).is_absolute() or '..' in Path(name).parts or '.git' in Path(name).parts:
            raise ValueError('Unsafe cleanup path')
        if p.parent.resolve() != root and root not in p.parent.resolve().parents:
            raise ValueError('Cleanup path parent escapes checkout')
    # A rename across the test/production boundary cannot be treated as an
    # independent test hunk. Refuse rather than silently alter its semantics.
    status = git(root, 'diff', '--name-status', '-z', '--find-renames', 'HEAD')
    if status.returncode:
        raise ValueError('Cannot inspect rename boundaries')
    fields = status.stdout.decode().split('\0'); pos = 0
    while pos < len(fields) and fields[pos]:
        kind = fields[pos]; pos += 1
        names = fields[pos:pos + (2 if kind.startswith(('R', 'C')) else 1)]
        pos += len(names)
        if len(names) == 2 and is_test_path(names[0]) != is_test_path(names[1]):
            raise ValueError('Test/production rename requires explicit review; automatic cleanup refused')
    pathspecs = [':(literal)' + p for p in production]
    before = git(root, 'diff', '--binary', '--no-ext-diff', 'HEAD', '--', *pathspecs)
    if before.returncode or not before.stdout.strip():
        raise ValueError('Cannot preserve a nonempty production diff')
    removed = []
    for name in forbidden:
        exists_at_base = git(root, 'cat-file', '-e', 'HEAD:' + name).returncode == 0
        if exists_at_base:
            result = git(root, 'checkout', 'HEAD', '--', ':(literal)' + name)
        else:
            # git rm handles a newly staged file. It cannot touch another path.
            result = git(root, 'rm', '-f', '--', ':(literal)' + name)
        if result.returncode:
            raise ValueError('Could not restore test path: ' + name + ': ' + result.stderr.decode(errors='replace')[:500])
        removed.append({'path': name, 'action': 'restored_base_index_and_worktree' if exists_at_base else 'removed_new_test'})
    after = git(root, 'diff', '--binary', '--no-ext-diff', 'HEAD', '--', *pathspecs)
    cleaned = git(root, 'diff', '--binary', '--no-ext-diff', 'HEAD')
    if after.returncode or cleaned.returncode or after.stdout != before.stdout or cleaned.stdout != before.stdout:
        raise ValueError('Production diff preservation check failed; original patch remains archived')
    clean_path = Path(patch_path).with_suffix('.production.patch')
    clean_path.write_bytes(cleaned.stdout)
    validation = inspect_patch(root, clean_path)
    if not validation['valid']:
        raise ValueError('Normalized submission still fails mechanical validation')
    return {'patch': cleaned.stdout.decode(), 'original_patch_sha256': check['patch_sha256'],
            'patch_sha256': validation['patch_sha256'], 'excluded_test_paths': removed,
            'production_diff_unchanged': True,
            'production_diff_sha256': hashlib.sha256(before.stdout).hexdigest(),
            'mechanical_validation': validation, 'model_calls_used': 0}

if __name__ == "__main__":
    import sys
    request = json.loads(sys.argv[1])
    operation = request.pop("operation")
    if operation not in ("inspect_patch", "normalize_test_submission"):
        raise ValueError("Unsupported submission operation")
    print(json.dumps(globals()[operation](**request)))
