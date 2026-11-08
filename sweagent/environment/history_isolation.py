"""Replace Git history with a single source snapshot in disposable location containers."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def isolate_history(repo, expected_base, *, disposable=False):
    if not disposable:
        raise ValueError('Only disposable checkouts may be sanitized')
    root = Path(repo).resolve()
    if root == Path('/') or not (root/'.git').is_dir() or (root/'.git').is_symlink():
        raise ValueError('Expected a standalone disposable Git worktree')
    def git(*args, check=True):
        return subprocess.run(['git', *args], cwd=root, check=check, capture_output=True, timeout=480)
    current = git('rev-parse', 'HEAD').stdout.decode().strip()
    expected = git('rev-parse', expected_base+'^{commit}').stdout.decode().strip()
    if current != expected:
        raise ValueError('Checkout does not match the task base commit')
    # Snapshot the checked-out tracked files; expand initialized submodules.
    tracked = [os.fsdecode(p) for p in git('ls-files', '-z').stdout.split(b'\0') if p]
    paths = set()
    for name in tracked:
        path = root/name
        if path.is_dir() and not path.is_symlink():
            for directory, dirs, files in os.walk(path, followlinks=False):
                dirs[:] = [d for d in dirs if d != '.git']
                for file in files:
                    if file != '.git':
                        paths.add(str((Path(directory)/file).relative_to(root)))
        elif path.is_file() or path.is_symlink():
            paths.add(name)
        else:
            raise ValueError('Tracked source is missing: '+name)
    def digest():
        sha = hashlib.sha256()
        for name in sorted(paths):
            path = root/name
            sha.update(os.fsencode(name)+b'\0')
            if path.is_symlink():
                sha.update(b'link\0'+os.fsencode(os.readlink(path)))
            else:
                sha.update(b'file\0'+path.read_bytes())
            sha.update(b'\0')
        return sha.hexdigest()
    before = digest()
    # Remove metadata only inside this disposable container's checkout. Never
    # retain the old objects/remotes in an agent-readable backup directory.
    metadata = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        if '.git' in dirs:
            dirs.remove('.git')
            metadata.append(Path(directory)/'.git')
        if '.git' in files:
            metadata.append(Path(directory)/'.git')
    for path in metadata:
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path)
    git('init', '--quiet', '--template=')
    git('symbolic-ref', 'HEAD', 'refs/heads/current-checkout')
    git('config', 'user.name', 'PhoenixRepair Snapshot')
    git('config', 'user.email', 'snapshot@localhost')
    files = sorted(paths)
    for offset in range(0, len(files), 100):
        git('add', '--force', '--', *files[offset:offset+100])
    git('-c', 'commit.gpgsign=false', 'commit', '--quiet', '--allow-empty', '-m', 'Current checkout only')
    after = digest()
    if before != after:
        raise RuntimeError('Source content changed during isolation')
    count = int(git('rev-list', '--all', '--count').stdout)
    remotes = git('remote').stdout.decode().strip()
    original_accessible = git('cat-file', '-e', expected+'^{commit}', check=False).returncode == 0
    if count != 1 or remotes or original_accessible:
        raise RuntimeError('Git history isolation verification failed')
    return {'mode': 'current_checkout_only_v1', 'tracked_files': len(paths),
            'source_sha256_before': before, 'source_sha256_after': after,
            'source_unchanged': True, 'accessible_commit_count': count, 'remotes': [],
            'original_commit_accessible': False,
            'snapshot_commit': git('rev-parse', 'HEAD').stdout.decode().strip()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('repo')
    parser.add_argument('expected_base')
    args = parser.parse_args()
    if not Path('/.dockerenv').exists():
        raise SystemExit('This command is restricted to disposable Docker containers')
    print(json.dumps(isolate_history(args.repo, args.expected_base, disposable=True)))
