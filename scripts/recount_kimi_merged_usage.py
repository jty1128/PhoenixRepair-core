"""Publish aggregate Kimi merge accounting from private, hash-checked evidence.

The selected-results and merge-audit inputs come from the per-task merge audit.
No task IDs, local paths, credentials, prompts or patches enter the output.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
from decimal import Decimal as D
import hashlib
import json
import os
from pathlib import Path
import sqlite3

FX = D('6.7168')
TOKEN_KEYS = ('total_tokens', 'input_tokens', 'cached_input_tokens',
              'uncached_input_tokens', 'output_tokens')
STAGES = ('sample-1', 'sample-2', 'sample-3', 'final')
LABELS = ('Sample 1', 'Sample 2', 'Sample 3', 'TTS Final')


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def total(rows):
    result = Counter()
    for row in rows:
        result.update(row)
    return result


def raw_usage(usage):
    i, o = usage['prompt_tokens'], usage['completion_tokens']
    details = usage.get('prompt_tokens_details', {})
    c = details.get('cached_tokens', 0)
    created = details.get('cache_creation_input_tokens', 0)
    explicit = details.get('cache_type') == 'ephemeral'
    assert not created or explicit
    assert 0 <= c <= i and 0 <= created <= i-c
    assert usage['total_tokens'] == i+o
    return Counter(input_tokens=i, output_tokens=o, total_tokens=i+o,
                   cached_input_tokens=c, uncached_input_tokens=i-c,
                   ordinary_input=i-c-created, cache_creation=created,
                   explicit_cache=c if explicit else 0,
                   implicit_cache=0 if explicit else c, requests_with_usage=1)


def historical_usage(u):
    i, c = u['input_tokens'], u['cached_input_tokens']
    created = u['cache_creation_input_tokens']
    result = Counter({k: u.get(k, 0) for k in TOKEN_KEYS})
    result.update(uncached_input_tokens=i-c, ordinary_input=i-c-created,
                  cache_creation=created, explicit_cache=u.get('explicit_cached_input_tokens', 0),
                  implicit_cache=u.get('implicit_cached_input_tokens', 0),
                  requests_with_usage=u['requests_with_usage'],
                  requests_without_usage=u['requests_without_usage'])
    assert result['explicit_cache']+result['implicit_cache'] == c
    assert abs(costs(result)['total_usd'] - D(u['known_usage_list_cost_usd'])) < D('1e-22')
    return result


def ledger_usage(path):
    pid_tasks = {}
    for p in path.parent.glob('kimi-k2.7-code/*/*/attempts/*/process.json'):
        pid = read(p)['pid']
        iid = read(p.parent/'request.json')['task']['instance_id']
        assert pid not in pid_tasks or pid_tasks[pid] == iid
        pid_tasks[pid] = iid
    source = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
    snapshot = sqlite3.connect(':memory:')
    source.backup(snapshot)
    source.close()
    snapshot.row_factory = sqlite3.Row
    by_task = defaultdict(Counter)
    for r in snapshot.execute('SELECT * FROM requests'):
        u = by_task[pid_tasks[r['pid']]]
        if not r['usage_available']:
            u['requests_without_usage'] += 1
            continue
        i, o, c = r['input_tokens'], r['output_tokens'], r['cached_input_tokens']
        assert isinstance(c, int) and 0 <= c <= i and r['total_tokens'] == i+o
        # The normalized ledger dropped cache_type and cache creation counts.
        # Do not infer a provider billing mode from cache_control in the config.
        u.update(input_tokens=i, output_tokens=o, total_tokens=i+o,
                 cached_input_tokens=c, uncached_input_tokens=i-c,
                 unknown_cache=c, unknown_uncached=i-c, requests_with_usage=1)
    snapshot.close()
    return dict(by_task)


def costs(u):
    scale = D(1_000_000)*FX
    ordinary = D(u.get('ordinary_input', 0))*D('6.5')
    creation = D(u.get('cache_creation', 0))*D('8.125')
    known_cache = D(u.get('implicit_cache', 0))*D('1.3') + D(u.get('explicit_cache', 0))*D('.65')
    unknown_i, unknown_c = u.get('unknown_uncached', 0), u.get('unknown_cache', 0)
    # A fixed public-price convention, not a claim about provider invoice debits.
    # Preserve observed cache modes; price missing modes as ordinary/implicit.
    result = {
        'uncached_input_usd': (ordinary+creation+unknown_i*D('6.5'))/scale,
        'cached_input_usd': (known_cache+unknown_c*D('1.3'))/scale,
        'output_usd': D(u['output_tokens'])*27/scale,
    }
    result['total_usd'] = sum(result.values())
    return result


def row(label, passed, usage):
    fees = costs(usage)
    return dict(experiment=label, tasks=51, passed=passed,
                token_totals={k: usage.get(k, 0) for k in TOKEN_KEYS},
                public_price_cost_usd_totals={k: str(v) for k, v in fees.items()})


def match_original_summaries(root, ledger_path, selected_final, original_usage):
    """Require a unique task/response bijection; never use nearest-time guessing."""
    database, = read(ledger_path)['databases']
    rows = database['rows']
    assert len({r['id'] for r in rows}) == len(rows)
    successes = {r['id']: r for r in rows if r['status'] == 'success'}
    assert len(successes) == 51
    assert all(r['usage_available'] for r in successes.values())
    assert all(not r['usage_available'] for r in rows if r['status'] != 'success')
    directory = root/'PhoenixRepair/validation_runs/pro-v2-hard51-kimi-k2.7-code-thinking-final-sample-51-20261106/summaries'
    metadata = {}
    for p in directory.glob('*/summary_result.json'):
        m = read(p)
        assert m['status'] == 'completed' and m['attempts'] == 1
        assert m['summary_sha256'] == digest(p.parent/'summary.txt')
        assert m['prompt_sha256'] == digest(p.parent/'summary_prompt.txt')
        metadata[m['instance_id']] = m
    assert set(metadata) == set(selected_final)
    stamp = lambda value: datetime.fromisoformat(value).timestamp()
    candidates = {iid: [rid for rid, r in successes.items()
                        if 0 <= stamp(m['finished_at'])-stamp(r['finished_at']) < 2]
                  for iid, m in metadata.items()}
    account_keys = defaultdict(set)
    for iid, matches in candidates.items():
        if len(matches) == 1:
            account_keys[metadata[iid]['account']].add(successes[matches[0]]['key_id'])
    assert all(len(keys) == 1 for keys in account_keys.values())
    account_keys = {a: next(iter(keys)) for a, keys in account_keys.items()}
    candidates = {iid: [rid for rid in matches if successes[rid]['key_id'] == account_keys[metadata[iid]['account']]]
                  for iid, matches in candidates.items()}
    matched = {}

    def propagate():
        while candidates:
            singles = {iid: matches[0] for iid, matches in candidates.items() if len(matches) == 1}
            if not singles:
                break
            assert len(set(singles.values())) == len(singles)
            matched.update(singles)
            used = set(singles.values())
            for iid in singles:
                del candidates[iid]
            for iid in candidates:
                candidates[iid] = [rid for rid in candidates[iid] if rid not in used]
                assert candidates[iid], 'Conflicting summary-to-ledger evidence'

    propagate()
    completion_matches = len(matched)
    event_path = root/'analysis/kimi_summary_final_20261106/events.jsonl'
    starts = defaultdict(list)
    for line in event_path.read_text().splitlines():
        event = json.loads(line)
        if event['kind'] == 'summary开始':
            starts[event['instance_id']].append(event)
    # Two completions crossed within 0.1 seconds. Their independently recorded
    # start events are 39 seconds apart and uniquely identify the API requests.
    for iid, matches in candidates.items():
        events = [e for e in starts[iid] if e['account'] == metadata[iid]['account']]
        candidates[iid] = [rid for rid in matches if any(
            0 <= stamp(successes[rid]['started_at'])-stamp(e['time']) < 2 for e in events)]
        assert candidates[iid], 'Ambiguous summary needs additional start evidence'
    propagate()
    assert not candidates and set(matched.values()) == set(successes)

    def usage(r):
        return raw_usage(dict(prompt_tokens=r['input_tokens'], completion_tokens=r['output_tokens'],
                              total_tokens=r['total_tokens'], prompt_tokens_details={'cached_tokens': r['cached_input_tokens']}))

    all_usage = total(usage(r) for r in successes.values())
    for k in TOKEN_KEYS:
        assert all_usage[k] == original_usage[k]
    keep = {iid: rid for iid, rid in matched.items() if selected_final[iid]['source'] != '2h_new_final'}
    assert len(keep) == 28
    retained = total(usage(successes[rid]) for rid in keep.values())
    proof = dict(original_successes=51, retained_summaries=28, replaced_summaries=23,
                 matched_by_completion_and_account=completion_matches,
                 additionally_matched_by_start_events=len(matched)-completion_matches,
                 bijection_verified=True, start_events_sha256=digest(event_path))
    return retained, proof


def tables(rows):
    lines = ['| 实验 | 成绩 | 总 tokens / task | Input tokens / task（含缓存） | 缓存 input tokens / task | 非缓存 input tokens / task | Output tokens / task |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in rows:
        cells = [r['experiment'], f"{r['passed']}/51"]
        cells += [f"{D(r['token_totals'][k])/51:,.2f}" for k in TOKEN_KEYS]
        lines.append('| '+' | '.join(cells)+' |')
    lines += ['', '分项计费（美元 USD；实际 Token 按下述固定口径折算的公开价费用）：', '',
              '| 实验 | 非缓存 input 费用 / task | 缓存 input 费用 / task | Output 费用 / task | 合计费用 / task | 51 题总费用 |',
              '| --- | ---: | ---: | ---: | ---: | ---: |']
    for r in rows:
        cells = [r['experiment']]
        cells += [f"${D(r['public_price_cost_usd_totals'][k])/51:,.4f}"
                  for k in ('uncached_input_usd', 'cached_input_usd', 'output_usd', 'total_usd')]
        cells.append(f"${D(r['public_price_cost_usd_totals']['total_usd']):,.2f}")
        lines.append('| '+' | '.join(cells)+' |')
    return '\n'.join(lines)+'\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('selected-results', 'merge-audit', 'original-audit', 'original-root',
                 'context-run', 'retry-run', 'summary-ledger', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    selected, audit, base = map(read, (args.selected_results, args.merge_audit, args.original_audit))

    def local(path):
        # Relocate original absolute backup symlinks without modifying evidence.
        for _ in range(12):
            for parent in [*reversed(path.parents), path]:
                if parent.is_symlink():
                    target = os.readlink(parent)
                    if '/TSE-Revision/' in target:
                        path = args.original_root/target.split('/TSE-Revision/', 1)[1]/path.relative_to(parent)
                        break
            else:
                return path
        raise ValueError('Symlink loop')

    original = {k: historical_usage(v['provider_usage']) for k, v in base['stages'].items()}
    old = {g: ledger_usage(args.context_run/g/'api-usage.sqlite3') for g in STAGES[:3]}
    new = {g: ledger_usage(args.retry_run/g/'api-usage.sqlite3') for g in STAGES}
    picked, cumulative = {}, {}
    for n, g in enumerate(STAGES):
        uses = []
        assert len(selected[g]) == 51
        assert set(selected[g]) == set(selected['sample-1'])
        for iid, item in selected[g].items():
            patch = local(Path(item['patch']))
            evaluation = read(local(Path(item['evaluation'])))
            assert digest(patch) == item['patch_sha256'] == evaluation['patch_sha256']
            assert item['passed'] == evaluation['resolved']
            assert evaluation['status'] in ('resolved', 'unresolved')
            assert evaluation.get('apply_returncode', 0) == 0
            source = item['source']
            assert source in ('original', 'original_retry8', 'context_rerun', '2h_retry', '2h_new_final')
            if source.startswith('original'):
                u = total(raw_usage(json.loads(line)['usage'])
                          for line in (patch.parent/'kimi_requests.jsonl').read_text().splitlines())
            else:
                u = (old if source == 'context_rerun' else new)[g][iid]
            for k in TOKEN_KEYS:
                if k != 'uncached_input_tokens':
                    assert u[k] == item['usage'][k]
            uses.append(u)
        picked[g] = total(uses)
        u = picked[g]
        assert u['ordinary_input']+u['cache_creation']+u['unknown_uncached'] == u['uncached_input_tokens']
        assert u['explicit_cache']+u['implicit_cache']+u['unknown_cache'] == u['cached_input_tokens']
        assert u['uncached_input_tokens']+u['cached_input_tokens'] == u['input_tokens']
        assert u['input_tokens']+u['output_tokens'] == u['total_tokens']
        previous = [original[f'sample{n+1}']] if n < 3 else [original['final_original'], original['retry8']]
        cumulative[g] = total(previous + list(old.get(g, {}).values()) + list(new[g].values()))
        recorded = audit['rows'][n]
        assert recorded['stage'] == g
        assert sum(x['passed'] for x in selected[g].values()) == recorded['passed']
        assert Counter(x['source'] for x in selected[g].values()) == recorded['source_counts']
        for k in TOKEN_KEYS:
            if k != 'uncached_input_tokens':
                assert picked[g][k] == recorded['selected_stage_usage'][k]
                assert cumulative[g][k] == recorded['cumulative_stage_usage'][k]

    for iid, item in selected['final'].items():
        if item['source'] == '2h_new_final':
            provenance = read(args.retry_run/'final/kimi-k2.7-code'/iid/'candidate-provenance.json')
            assert len(provenance) == 3
            for n, candidate in enumerate(provenance, 1):
                assert candidate['patch_sha256'] == selected[f'sample-{n}'][iid]['patch_sha256']

    summary = Counter()
    paths = list(args.retry_run.glob('final/kimi-k2.7-code/*/summary/usage.json'))
    assert len(paths) == 23
    for path in paths:
        raw = total(raw_usage(read(p)['usage']) for p in path.parent.glob('response-*.json'))
        recorded = read(path)
        assert raw['input_tokens'] == recorded['prompt_tokens']
        assert raw['output_tokens'] == recorded['completion_tokens']
        raw['requests_without_usage'] = recorded['api_attempts']-raw['requests_with_usage']
        summary.update(raw)
    assert summary['cached_input_tokens'] == 0
    retained_summary, summary_proof = match_original_summaries(
        args.original_root, args.summary_ledger, selected['final'], original['summary'])
    selected_full = total([*picked.values(), summary, retained_summary])
    cumulative_summary = total([summary, original['summary']])
    full = total([*cumulative.values(), cumulative_summary])
    assert full['total_tokens'] == audit['cumulative_tts_full_pipeline_usage']['total_tokens']
    assert full['requests_without_usage'] == audit['cumulative_tts_full_pipeline_usage']['requests_without_usage']
    selected_rows = [row(LABELS[n], audit['rows'][n]['passed'], picked[g]) for n, g in enumerate(STAGES[:3])]
    selected_rows.append(row('TTS Final', audit['rows'][3]['passed'], selected_full))
    result = dict(model='kimi-k2.7-code', benchmark='SWE-bench Pro v2 HARD-51', as_of='2026-11-09',
                  selected_rows=selected_rows,
                  merge=[{k: r[k] for k in ('stage', 'tasks', 'passed', 'original_passed', 'gained', 'lost', 'source_counts')}
                         for r in audit['rows']],
                  selected_generation_usage={g: {k: v for k, v in u.items() if k != 'requests_without_usage'}
                                             for g, u in picked.items()},
                  selected_rerun_requests_without_usage={g: u['requests_without_usage'] for g, u in picked.items()},
                  selected_rerun_exit_status_counts={g: dict(Counter(x['exit_status'] for x in selected[g].values()
                                                                    if not x['source'].startswith('original')))
                                                    for g in STAGES},
                  new23_summary_usage=summary, original51_summary_usage=original['summary'],
                  retained28_original_summary_usage=retained_summary,
                  original_summary_matching=summary_proof,
                  pricing=dict(base['pricing'], checked_date='2026-11-09',
                               unknown_cache_mode_convention='implicit cache hit and ordinary uncached input',
                               cost_kind='public-price estimate using actual recorded tokens, not invoiced charges'),
                  evidence_sha256={k: digest(getattr(args, k)) for k in ('selected_results', 'merge_audit', 'original_audit', 'summary_ledger')},
                  notes=[
                      'Latest designated rerun replaces the earlier result regardless of evaluation score; not best-of selection.',
                      'Selected TTS is merged sample1/2/3 + 23 new summaries + merged final generation + exactly matched 28 retained original summaries.',
                      'All 51 original successful summary responses uniquely matched to task metadata using completion times, account mapping, one-to-one constraints and start events. No proportional allocation or nearest-time guessing.',
                      'New ledgers retain token counts but not cache_type or cache creation. For a single reproducible public-price estimate, unknown cache is assumed implicit at CNY 1.3/M and unknown uncached input is priced as ordinary input at CNY 6.5/M.',
                      'Noncached fee includes known explicit cache creation at CNY 8.125/M; known original cache modes priced from raw usage.',
                      'Fees use actual recorded tokens under the stated pricing convention; requests without usage may incur additional costs. These public-price estimates are not invoiced charges.',
                      'Superseded runs are excluded. Recorded internal recovery attempts of each selected task run are included. Evaluation/server costs are excluded.',
                      'Per-task missing-usage counts are available only for selected reruns, not for retained original tasks. Do not interpret rerun counts as a complete pipeline count.',
                      'Every displayed per-task value is divided by 51, including failures; TTS already includes all three sample rows.',
                      'Input includes cache hits; output includes thinking, which is not added again. Mixed-budget merged results, not uniform-budget pass@1.',
                  ])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    with args.output.with_suffix('.csv').open('w', newline='') as f:
        writer = csv.writer(f, lineterminator='\n')
        writer.writerow(['scope', 'experiment', 'passed', 'tasks', 'metric', 'actual_tokens_total', 'actual_tokens_per_task', 'public_price_cost_usd_total', 'public_price_cost_usd_per_task'])
        for r in selected_rows:
            for k, value in r['token_totals'].items():
                writer.writerow(['selected', r['experiment'], r['passed'], 51, k, value, str(D(value)/51), '', ''])
            for k, value in r['public_price_cost_usd_totals'].items():
                writer.writerow(['selected', r['experiment'], r['passed'], 51, k, '', '', value, str(D(value)/51)])
    args.output.with_suffix('.md').write_text('### 最终保留结果\n\n'+tables(selected_rows))
    print(json.dumps({'scores': {r['experiment']: r['passed'] for r in selected_rows},
                      'selected_tts_tokens': selected_rows[-1]['token_totals'],
                      'retained28_original_summary_tokens': {k: retained_summary[k] for k in TOKEN_KEYS},
                      'summary_matching': summary_proof,
                      'selected_tts_public_price_usd': selected_rows[-1]['public_price_cost_usd_totals']['total_usd']}, indent=2))


if __name__ == '__main__':
    main()
