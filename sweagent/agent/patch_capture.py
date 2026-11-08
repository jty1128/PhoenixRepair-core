"""Pro v2 staged-diff extraction, with checked errors and a host-adapter path.

Matches locked_mini_swe._CAPTURE's add -A / diff --cached / reset sequence.
Process stopping belongs to the caller. No extra diff flags or file filtering.
"""
import shlex


def staged_diff_command(repo: str, destination: str = '/root/model.patch') -> str:
    target = shlex.quote(destination)
    return (f'cd {shlex.quote(repo)} && rm -f {target} && git add -A && '
            f'git diff --cached > {target} && git reset -q')
