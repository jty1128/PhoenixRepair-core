"""Classify model transport failures without treating tool/runtime errors as API failures."""
import shlex
import ssl

import httpx
import litellm
import openai
import requests


def is_api_error(error: BaseException) -> bool:
    return isinstance(error, (
        openai.APIError, litellm.exceptions.APIError,
        httpx.TransportError, requests.exceptions.RequestException,
        ConnectionError, TimeoutError, ssl.SSLError,
    ))


def is_retryable_api_error(error: BaseException) -> bool:
    # Certificates, credentials, invalid schemas and context limits require a
    # configuration change. Retrying the identical request cannot repair them.
    cause, seen = error, set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, ssl.SSLCertVerificationError) or "certificate verify failed" in str(cause).lower():
            return False
        cause = cause.__cause__ or cause.__context__
    if not is_api_error(error):
        return False
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        return status in (408, 429) or 500 <= status < 600
    return isinstance(error, (
        openai.APIConnectionError, httpx.TimeoutException, httpx.NetworkError,
        requests.exceptions.Timeout, requests.exceptions.ConnectionError,
        ConnectionError, TimeoutError,
    ))


def interrupted_patch_command(repo: str) -> str:
    """Capture tracked/untracked edits through a private index, without submitting."""
    return (
        f"(cd {shlex.quote(repo)} && phoenix_api_index=$(mktemp) && "
        'rm -f "$phoenix_api_index" && '
        "trap 'rm -f \"$phoenix_api_index\" \"$phoenix_api_index.lock\"' EXIT && "
        'GIT_INDEX_FILE="$phoenix_api_index" git read-tree HEAD && '
        'GIT_INDEX_FILE="$phoenix_api_index" git add -A && '
        'GIT_INDEX_FILE="$phoenix_api_index" git diff --cached --binary HEAD)'
    )
