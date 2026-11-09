"""Publish aggregate Kimi merge accounting from private, hash-checked evidence.

The selected-results and merge-audit inputs come from the per-task merge audit.
No task IDs, local paths, credentials, prompts or patches enter the output.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
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
    assert abs(costs(result)['total_usd'][0] - D(u['known_usage_list_cost_usd'])) < D('1e-22')
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
    result = {
        'uncached_input_usd': [(ordinary+creation+unknown_i*D(rate))/scale for rate in ('6.5', '8.125')],
        'cached_input_usd': [(known_cache+unknown_c*D(rate))/scale for rate in ('.65', '1.3')],
        'output_usd': [D(u['output_tokens'])*27/scale]*2,
    }
    result['total_usd'] = [sum(v[n] for v in result.values()) for n in (0, 1)]
    return result


def row(label, passed, lower, upper=None):
    upper = lower if upper is None else upper
    token_bounds = {k: [lower.get(k, 0), upper.get(k, 0)] for k in TOKEN_KEYS}
    lo, hi = costs(lower), costs(upper)
    fee_bounds = {k: [str(lo[k][0]), str(hi[k][1])] for k in lo}
    return dict(experiment=label, tasks=51, passed=passed,
                token_totals_bounds=token_bounds, cost_usd_totals_bounds=fee_bounds)


def interval(values, divisor=1, places=2, currency=False):
    formatted = [('$' if currency else '') + f'{D(v)/divisor:,.{places}f}' for v in values]
    return ('–'.join(formatted) if formatted[0] != formatted[1] else formatted[0]).strip()


def tables(rows):
    lines = ['| 实验 | 成绩 | 总 tokens / task | Input tokens / task（含缓存） | 缓存 input tokens / task | 非缓存 input tokens / task | Output tokens / task |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in rows:
        cells = [r['experiment'], f"{r['passed']}/51"]
        cells += [interval(r['token_totals_bounds'][k], 51) for k in TOKEN_KEYS]
        lines.append('| '+' | '.join(cells)+' |')
    lines += ['', '分项计费（美元 USD；区间为已记录用量的公开价估算）：', '',
              '| 实验 | 非缓存 input 费用 / task | 缓存 input 费用 / task | Output 费用 / task | 合计费用 / task | 51 题总费用 |',
              '| --- | ---: | ---: | ---: | ---: | ---: |']
    for r in rows:
        cells = [r['experiment']]
        cells += [interval(r['cost_usd_totals_bounds'][k], 51, 4, True)
                  for k in ('uncached_input_usd', 'cached_input_usd', 'output_usd', 'total_usd')]
        cells.append(interval(r['cost_usd_totals_bounds']['total_usd'], 1, 2, True))
        lines.append('| '+' | '.join(cells)+' |')
    return '\n'.join(lines)+'\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('selected-results', 'merge-audit', 'original-audit', 'original-root',
                 'context-run', 'retry-run', 'output'):
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
    lower = total([*picked.values(), summary])
    upper = total([lower, original['summary']])
    cumulative_summary = total([summary, original['summary']])
    full = total([*cumulative.values(), cumulative_summary])
    assert full['total_tokens'] == audit['cumulative_tts_full_pipeline_usage']['total_tokens']
    assert full['requests_without_usage'] == audit['cumulative_tts_full_pipeline_usage']['requests_without_usage']
    selected_rows = [row(LABELS[n], audit['rows'][n]['passed'], picked[g]) for n, g in enumerate(STAGES[:3])]
    selected_rows.append(row('TTS Final', audit['rows'][3]['passed'], lower, upper))
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
                  pricing=dict(base['pricing'], checked_date='2026-11-09'),
                  evidence_sha256={k: digest(getattr(args, k)) for k in ('selected_results', 'merge_audit', 'original_audit')},
                  notes=[
                      'Latest designated rerun replaces the earlier result regardless of evaluation score; not best-of selection.',
                      'Selected TTS is merged sample1/2/3 + 23 new summaries + merged final generation + unknown share of 28 old summaries.',
                      'Old summary allocation bounds are zero to the entire original 51-summary aggregate; no proportional allocation.',
                      'New ledgers retain token counts but not cache_type or cache creation; unknown cache priced at CNY 0.65–1.3/M, unknown uncached at CNY 6.5–8.125/M.',
                      'Noncached fee includes known explicit cache creation at CNY 8.125/M; known original cache modes priced from raw usage.',
                      'Bounds apply only to recorded usage; requests without usage may incur additional costs. Estimates are not invoices.',
                      'Superseded runs are excluded. Recorded internal recovery attempts of each selected task run are included. Evaluation/server costs are excluded.',
                      'Per-task missing-usage counts are available only for selected reruns, not for retained original tasks. Do not interpret rerun counts as a complete pipeline count.',
                      'Every displayed per-task value is divided by 51, including failures; TTS already includes all three sample rows.',
                      'Input includes cache hits; output includes thinking, which is not added again. Mixed-budget merged results, not uniform-budget pass@1.',
                  ])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    with args.output.with_suffix('.csv').open('w', newline='') as f:
        writer = csv.writer(f, lineterminator='\n')
        writer.writerow(['scope', 'experiment', 'passed', 'tasks', 'metric', 'lower_total', 'upper_total', 'lower_per_task', 'upper_per_task'])
        for r in selected_rows:
            for k, bounds in dict(r['token_totals_bounds'], **r['cost_usd_totals_bounds']).items():
                writer.writerow(['selected', r['experiment'], r['passed'], 51, k, *bounds, *(str(D(v)/51) for v in bounds)])
    args.output.with_suffix('.md').write_text('### 最终保留结果\n\n'+tables(selected_rows))
    print(json.dumps({'scores': {r['experiment']: r['passed'] for r in selected_rows},
                      'selected_tts_tokens': selected_rows[-1]['token_totals_bounds']['total_tokens'],
                      'selected_tts_usd': selected_rows[-1]['cost_usd_totals_bounds']['total_usd']}, indent=2))


if __name__ == '__main__':
    main()
