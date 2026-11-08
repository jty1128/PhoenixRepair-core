"""Run source-only localization with the same workflow/tools as PhoenixRepair.

The host issues model requests. Read-only tools see only tracked files exported
from each official image's verified base checkout. There is no patch/test tool.
Account concurrency is bounded across requests/retries, not just HTTP calls.
"""
import argparse
import concurrent.futures
import hashlib
import io
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import tarfile
import threading
import time
import uuid

import requests
import yaml
from jinja2 import Template

from phoenix.localization_workflow import LocalizationWorkflow, MARKER
from sweagent.agent.context_compaction import compact_tool_outputs
from phoenix.localization_recovery import request_schema, recovery_message, compact_rejected_turns

ROOT = Path(__file__).resolve().parents[1]
LIB = runpy.run_path(str(ROOT / 'tools/location_workflow/bin/location_lib.py'))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    tmp.replace(path)


def source_key(instance):
    return hashlib.sha256(('\0'.join(instance[k] for k in ('image_name', 'base_commit', 'repo_name'))).encode()).hexdigest()[:24]


def source_ready(cache, instance):
    p = cache / source_key(instance)
    return p if (p / 'source.json').is_file() else None


def export_source(cache, instance):
    """Export tracked checkout files, not git history, image tests or solutions."""
    dest = cache / source_key(instance)
    if source_ready(cache, instance):
        meta = json.loads((dest / 'source.json').read_text())
        if meta['image'] != instance['image_name'] or meta['base_commit'] != instance['base_commit']:
            raise ValueError('Source cache provenance mismatch')
        return dest
    work = cache / (dest.name + '.tmp-' + uuid.uuid4().hex)
    work.mkdir(parents=True)
    name = 'phoenix-source-' + uuid.uuid4().hex[:16]
    archive = work / 'source.tar'
    try:
        # git ls-files selects the checked-out tree, including tracked tests;
        # tar does not apply export-ignore/export-subst attributes.
        script = ('set -eu; cd "$1"; test "$(git rev-parse HEAD)" = "$2"; '
                  'git diff --quiet HEAD --; git ls-files -z | tar -cf - --null -T -')
        args = ['docker', 'run', '--rm', '--pull=never', '--name', name, '--network=none', '--read-only',
                '--cpus=1', '--memory=1g', '--pids-limit=64', '--user=0',
                '-e', 'GIT_CONFIG_COUNT=1', '-e', 'GIT_CONFIG_KEY_0=safe.directory',
                '-e', 'GIT_CONFIG_VALUE_0=/' + instance['repo_name'],
                '--label', 'phoenixrepair.purpose=source-only-localization',
                '--entrypoint', '/bin/sh', instance['image_name'], '-c', script,
                'export', '/' + instance['repo_name'].strip('/'), instance['base_commit']]
        with archive.open('wb') as stream:
            result = subprocess.run(args, stdout=stream, stderr=subprocess.PIPE, timeout=600)
        if result.returncode:
            raise RuntimeError('Source export failed: ' + result.stderr.decode(errors='replace')[-1600:])
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        repo = work / 'repo'
        repo.mkdir()
        count = 0
        with tarfile.open(archive) as tar:
            # Reject traversal/metadata and special files. Symlinks are omitted,
            # matching the read-only tool's explicit no-symlink source policy.
            for member in tar:
                p = Path(member.name)
                if p.is_absolute() or '..' in p.parts or any(part in LIB['EXCLUDED'] for part in p.parts):
                    continue
                target = repo / p
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(member) as src, target.open('wb') as dst:
                        shutil.copyfileobj(src, dst)
                    target.chmod(0o444)
                    count += 1
        archive.unlink()
        write(work / 'source.json', {'image': instance['image_name'], 'base_commit': instance['base_commit'],
              'tracked_archive_sha256': digest, 'files': count, 'network': 'none',
              'reference_or_verifier_material_mounted': False})
        work.rename(dest)
        return dest
    finally:
        # The container belongs exclusively to this exporter.
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=45)
        if work.exists():
            shutil.rmtree(work)


def definitions(config, names):
    tools = yaml.safe_load((ROOT / 'tools/location_workflow/config.yaml').read_text())['tools']
    result = []
    for name in names:
        if name == 'analysis_complete':
            description = 'Submit all complete strategies with shared requirement coverage and branch outcomes.'
            props, required = {}, []
        else:
            spec = tools[name]
            description = spec['docstring']
            props = {a['name']: {'type': a['type'], 'description': a['description']} for a in spec['arguments']}
            if 'request' in props:
                props['request'] = {**request_schema(name), 'description': props['request']['description'] +
                                    ' Pass this request directly as an object, not an escaped JSON string.'}
            required = [a['name'] for a in spec['arguments'] if a.get('required')]
        result.append({'type': 'function', 'function': {'name': name, 'description': description,
            'parameters': {'type': 'object', 'properties': props, 'required': required, 'additionalProperties': False}}})
    return result


def call_api(session, endpoint, key, payload, task, *, timeout=600, deadline=None):
    for attempt in range(5):
        try:
            remaining = deadline - time.monotonic() if deadline else timeout
            if remaining <= 0:
                raise RuntimeError('Localization wall-time limit reached')
            response = session.post(endpoint.rstrip('/') + '/chat/completions',
                headers={'Authorization': 'Bearer ' + key}, json=payload,
                timeout=(min(30, remaining), min(timeout, remaining)))
            if response.status_code in (408, 429, 500, 502, 503, 504):
                write(task / 'api_retry.json', {'http_status': response.status_code, 'attempt': attempt + 1, 'time': time.time()})
                if attempt < 4:
                    try:
                        delay = min(60, max(2 ** (attempt + 1), float(response.headers.get('Retry-After', 0))))
                    except ValueError:
                        delay = 2 ** (attempt + 1)
                    time.sleep(delay)
                    continue
            if not response.ok:
                # Server error messages may echo request metadata; never log headers/keys.
                raise RuntimeError(f'Model API HTTP {response.status_code}')
            return response.json()
        except (requests.ConnectionError, requests.Timeout):
            if attempt == 4:
                raise
            time.sleep(min(30, 2 ** (attempt + 1)))


def run_task(instance, source, output, profile, key, config, endpoint):
    task = output / profile['model'] / instance['instance_id']
    task.mkdir(parents=True, exist_ok=True)
    status = {'instance_id': instance['instance_id'], 'model': profile['model'], 'account': profile['account'],
              'status': 'running', 'started_at': time.time(), 'steps': 0,
              'patch_generation_run': False, 'tests_run': False}
    write(task / 'report.json', status)
    artifacts = task / 'artifacts'
    artifacts.mkdir(exist_ok=True)
    store = LIB['Store'](source / 'repo', artifacts)
    controller = LocalizationWorkflow(independent_hypotheses=True, complete_strategies=True)
    templates = config['agent']['templates']
    messages = [{'role': 'system', 'content': templates['system_template']},
                {'role': 'user', 'content': Template(templates['instance_template']).render(
                    working_dir='/current-checkout', problem_statement=instance['problem_statement'])}]
    usage = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0, 'api_calls': 0}
    transcript = task / 'trajectory.jsonl'
    limit = config['agent']['max_steps']
    runtime = config['localization_runtime']
    deadline = time.monotonic() + runtime['task_timeout']
    session = requests.Session()
    # No environment proxy/credential fallback to unrelated services.
    session.trust_env = False
    last_action, repeated = None, 0
    last_rejection = None
    rejected_ids = set()
    try:
        import tiktoken
        encoder = tiktoken.get_encoding('cl100k_base')
        def tokens(ms):
            return sum(len(encoder.encode(json.dumps(m, ensure_ascii=False), disallowed_special=())) + 6 for m in ms)
        for step in range(1, limit + 1):
            if time.monotonic() >= deadline:
                raise RuntimeError('Localization wall-time limit reached; drafts retained')
            control = controller.control(step, limit)
            write(artifacts / 'location_control.json', control)
            instruction = controller.message(control)
            if last_rejection:
                instruction += recovery_message(last_rejection, store.load(), repeated)
            request_messages = messages + [{'role': 'user', 'content': instruction}]
            # Preserve full original trajectory on disk; compact only API copies.
            if tokens(request_messages) > runtime['context_compaction_threshold']:
                request_messages, count, compacted = compact_tool_outputs(
                    request_messages, tokens, target_tokens=runtime['context_compaction_target'])
                request_messages, count, rejected = compact_rejected_turns(
                    request_messages, rejected_ids, tokens, target_tokens=runtime['context_compaction_target'])
                write(task / 'compaction.json', {'step': step, 'tokens': count, 'outputs_summarized': compacted,
                                               'rejected_exchanges_omitted': rejected})
                if count > runtime['max_input_tokens']:
                    raise RuntimeError('Context budget exceeded after preserving actions/requirements; drafts retained')
            choice = ({'type': 'function', 'function': {'name': 'location_checkpoint'}}
                      if control['checkpoint_required'] else runtime['tool_choice'])
            payload = dict(model=profile['model'], messages=request_messages,
                           tools=definitions(config, control['allowed_tools']), tool_choice=choice,
                           parallel_tool_calls=False, temperature=0.0, top_p=1.0,
                           max_tokens=runtime['max_tokens'], enable_thinking=runtime['enable_thinking'])
            response = call_api(session, endpoint, key, payload, task,
                                timeout=runtime['api_timeout'], deadline=deadline)
            usage['api_calls'] += 1
            for k in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
                usage[k] += response.get('usage', {}).get(k, 0) or 0
            answer = response['choices'][0]['message']
            message = {k: answer[k] for k in ('role', 'content', 'tool_calls', 'reasoning_content') if k in answer and answer[k] is not None}
            message.setdefault('role', 'assistant')
            messages.append(message)
            calls = answer.get('tool_calls') or []
            event, observations, final = {}, [], None
            if not calls:
                messages.append({'role': 'user', 'content': 'Use an allowed tool to inspect source, record complete strategies or submit. Prose alone is not a submission.'})
            for call in calls:
                name = call['function']['name']
                try:
                    normalized_args = json.loads(call['function']['arguments'])
                except (ValueError, TypeError):
                    normalized_args = call['function']['arguments']
                action = json.dumps([name, normalized_args], sort_keys=True)
                repeated = repeated + 1 if action == last_action else 1
                last_action = action
                try:
                    if len(calls) != 1:
                        raise ValueError('Use exactly one tool call per turn; none of these parallel calls executed')
                    if name not in control['allowed_tools']:
                        raise ValueError('Tool not permitted now. Allowed: ' + ', '.join(control['allowed_tools']) +
                                         '. Controller instruction: ' + control['instructions'])
                    args = json.loads(call['function']['arguments'])
                    if name == 'analysis_complete':
                        final = LIB['finalize_analysis'](store.load(), control)
                        for candidate in final['candidates']:
                            LIB['complete_strategy']()['validate_plan'](candidate, source=store.source)
                        observation, event = 'Complete strategies submitted (not verified repairs).', {'ok': True}
                    elif name == 'location_abort':
                        observation, event = store.abort(args['reason'])
                    else:
                        request, _ = LIB['decode_request'](args['request'])
                        observation, event = getattr(store, name.removeprefix('location_'))(request)
                    controller.observe(name, MARKER + json.dumps(event), step)
                except (ValueError, KeyError, TypeError, AttributeError, OSError, re.error) as exc:
                    observation, event = 'Localization tool error: ' + str(exc), {'ok': False}
                    final = None
                observations.append({'tool': name, 'observation': observation, 'event': event})
                if not event.get('ok'):
                    rejected_ids.add(call['id'])
                messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': observation + '\n' + MARKER + json.dumps(event)})
            with transcript.open('a') as stream:
                stream.write(json.dumps({'step': step, 'control': control, 'response': response,
                                         'observations': observations}, ensure_ascii=False) + '\n')
            last_rejection = '\n'.join(o['observation'] for o in observations if not o['event'].get('ok')) or None
            status.update(steps=step, updated_at=time.time(), usage=usage, candidate_count=controller.candidate_count)
            write(task / 'report.json', status)
            if final is not None:
                write(task / 'location_candidates.json', final)
                (task / 'locations.jsonl').write_text(''.join(json.dumps(c, ensure_ascii=False) + '\n' for c in final['candidates']))
                status.update(status='localized', candidates=len(final['candidates']),
                              exploration_outcome=final['exploration_outcome'],
                              unfinished_hypotheses=final['unfinished_hypotheses'])
                break
            if event.get('aborted'):
                status.update(status='failed', error='Model aborted: no complete candidate')
                break
            if runtime['action_repeat_limit'] > 0 and repeated >= runtime['action_repeat_limit']:
                raise RuntimeError('Repeated action limit reached; drafts retained')
        else:
            status.update(status='failed', error=f'{limit}-step limit without a validated submission; drafts retained')
    except Exception as exc:
        status.update(status='failed', error=re.sub(r'sk-[A-Za-z0-9_.-]+', '[REDACTED]', str(exc).replace(key, '[REDACTED]')))
    finally:
        session.close()
        status.update(finished_at=time.time(), usage=usage)
        write(task / 'report.json', status)
    return status


