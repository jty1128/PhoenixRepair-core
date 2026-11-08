"""Durable API usage ledger. Never stores keys, prompts, responses or error text."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from urllib.parse import urlsplit
import uuid


def database_path():
    return Path(os.environ.get('PHOENIX_API_USAGE_DB', str(Path.home() / 'api_usage/usage.sqlite3'))).expanduser()


def key_id(key):
    if hasattr(key, 'get_secret_value'):
        key = key.get_secret_value()
    return hashlib.sha256(str(key).encode()).hexdigest()[:16] if key else 'unknown'


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect(path=None):
    path = Path(path or database_path())
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Create with private permissions before sqlite opens the file.
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(fd)
    # WAL activation on a brand-new database can ignore SQLite's busy timeout.
    # Serialize schema initialization before any process opens that database.
    schema_fd = os.open(str(path) + '.schema.lock', os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(schema_fd, fcntl.LOCK_EX)
    con = None
    try:
        con = sqlite3.connect(path, timeout=30)
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('PRAGMA synchronous=FULL')
        con.execute('''CREATE TABLE IF NOT EXISTS requests (
            id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
            model TEXT, key_id TEXT, host TEXT, operation TEXT, task_id TEXT,
            stage TEXT, pid INTEGER, status TEXT NOT NULL, http_status INTEGER,
            error_type TEXT, duration_seconds REAL, input_tokens INTEGER,
            output_tokens INTEGER, total_tokens INTEGER, cached_input_tokens INTEGER,
            reasoning_tokens INTEGER, usage_available INTEGER NOT NULL DEFAULT 0,
            usage_json TEXT)''')
        con.execute('CREATE INDEX IF NOT EXISTS requests_started ON requests(started_at)')
        con.commit()
    except BaseException:
        if con is not None:
            con.close()
        raise
    finally:
        fcntl.flock(schema_fd, fcntl.LOCK_UN)
        os.close(schema_fd)
    try:
        yield con
    finally:
        con.close()


def as_dict(value):
    if isinstance(value, dict):
        return value
    if hasattr(value, 'model_dump'):
        return value.model_dump()
    if hasattr(value, 'to_dict'):
        return value.to_dict()
    return {}


def normalize_usage(response):
    data = as_dict(response)
    if isinstance(data.get('data'), dict) and isinstance(data['data'].get('response'), dict):
        data = data['data']['response']
    usage = as_dict(data.get('usage'))

    def integer(*values):
        return next((v for v in values if isinstance(v, int) and not isinstance(v, bool) and v >= 0), None)

    prompt = as_dict(usage.get('prompt_tokens_details') or usage.get('input_tokens_details'))
    completion = as_dict(usage.get('completion_tokens_details') or usage.get('output_tokens_details'))
    result = {
        'input_tokens': integer(usage.get('prompt_tokens'), usage.get('input_tokens')),
        'output_tokens': integer(usage.get('completion_tokens'), usage.get('output_tokens')),
        'total_tokens': integer(usage.get('total_tokens')),
        'cached_input_tokens': integer(prompt.get('cached_tokens'), usage.get('prompt_cache_hit_tokens')),
        'reasoning_tokens': integer(completion.get('reasoning_tokens')),
    }
    if result['total_tokens'] is None and result['input_tokens'] is not None and result['output_tokens'] is not None:
        result['total_tokens'] = result['input_tokens'] + result['output_tokens']
    return result


def error_status(http_status=None, error=None):
    name = type(error).__name__ if error else ''
    if http_status == 429 or 'RateLimit' in name:
        return 'rate_limited'
    if http_status == 401 or 'Authentication' in name:
        return 'auth_error'
    if 'Timeout' in name:
        return 'timeout'
    return 'error'


class UsageAttempt:
    def __init__(self, *, model, api_key, api_base, operation='completion', context=None):
        self.id = uuid.uuid4().hex
        self.path = database_path()
        self.started = time.monotonic()
        context = context or {}
        self.model, self.key = model, key_id(api_key)
        host = urlsplit(api_base or '').hostname or ''
        with connect(self.path) as con:
            con.execute('''INSERT INTO requests
                (id, started_at, model, key_id, host, operation, task_id, stage, pid, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')''',
                (self.id, now(), model, self.key, host, operation,
                 context.get('task_id', os.environ.get('PHOENIX_TASK_ID')),
                 context.get('stage', os.environ.get('PHOENIX_STAGE')), os.getpid()))
            con.commit()

    def finish(self, *, response=None, http_status=None, error=None):
        usage = normalize_usage(response)
        status = 'success' if error is None and (http_status is None or 200 <= http_status < 300) else error_status(http_status, error)
        values = dict(finished_at=now(), status=status, http_status=http_status,
                      error_type=type(error).__name__ if error else None,
                      duration_seconds=round(time.monotonic() - self.started, 4), **usage,
                      usage_available=int(usage['input_tokens'] is not None and usage['output_tokens'] is not None),
                      usage_json=json.dumps(usage))
        try:
            with connect(self.path) as con:
                con.execute('UPDATE requests SET ' + ', '.join(k + '=?' for k in values) + ' WHERE id=?',
                            [*values.values(), self.id])
                con.commit()
        except Exception:
            # Never turn a successful API response into another paid retry.
            # Leave the durable pending row to flag the unknown outcome.
            print(f'[API用量记录失败] request={self.id}；记录保留为 pending，请检查磁盘和账本。', file=sys.stderr, flush=True)
        if status != 'success':
            print(f'[API监控] model={self.model} key={self.key} status={status} HTTP={http_status}', file=sys.stderr, flush=True)


def tracked_completion(call, *, usage_context=None, **kwargs):
    attempt = UsageAttempt(model=kwargs.get('model'), api_key=kwargs.get('api_key'),
                           api_base=kwargs.get('api_base'), context=usage_context)
    try:
        response = call(**kwargs)
    except BaseException as exc:
        attempt.finish(error=exc, http_status=getattr(exc, 'status_code', None))
        raise
    attempt.finish(response=response, http_status=200)
    return response


def tracked_request(call, url, *, usage_context=None, usage_model=None, usage_operation='completion', **kwargs):
    auth = kwargs.get('headers', {}).get('Authorization', '')
    key = auth[7:] if auth.startswith('Bearer ') else None
    attempt = UsageAttempt(model=usage_model or (kwargs.get('json') or {}).get('model'),
                           api_key=key, api_base=url, operation=usage_operation, context=usage_context)
    try:
        response = call(url, **kwargs)
    except BaseException as exc:
        attempt.finish(error=exc, http_status=getattr(getattr(exc, 'response', None), 'status_code', None))
        raise
    try:
        data = response.json()
    except (ValueError, AttributeError):
        data = {}
    attempt.finish(response=data, http_status=response.status_code)
    return response
