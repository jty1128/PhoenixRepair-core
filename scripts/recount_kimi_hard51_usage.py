"""Audit retained Kimi HARD-51 runs against private provider-usage evidence.

Publishes aggregate data only. Never publishes credentials, accounts, task IDs,
prompts, trajectories, patches, process IDs, or ledger request IDs.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

RUNS = {
    "sample1": "pro-v2-hard51-kimi-k2.7-code-thinking-51-20261105",
    "sample2": "pro-v2-hard51-kimi-k2.7-code-thinking-sample2-51-20261105",
    "sample3": "pro-v2-hard51-kimi-k2.7-code-thinking-sample3-51-20261105",
    "final_original": "pro-v2-hard51-kimi-k2.7-code-thinking-final-sample-51-20261106",
    "retry8": "pro-v2-hard51-kimi-k2.7-code-thinking-final-retry8-2h-20261108",
}
EVALUATIONS = {
    "sample1": "kimi-k2.7-code-thinking-pro-v2-hard51-20261105",
    "sample2": "kimi-k2.7-code-thinking-pro-v2-hard51-sample2-20261105",
    "sample3": "kimi-k2.7-code-thinking-pro-v2-hard51-sample3-20261105",
    "final_original": "kimi-k2.7-code-thinking-pro-v2-hard51-final-sample-20261106",
    "retry8": "kimi-k2.7-code-thinking-pro-v2-hard51-final-retry8-2h-20261108",
}
FX = Decimal("6.7168")
RATES = (Decimal("6.5"), Decimal("1.3"), Decimal("27"))
EXPLICIT_CREATE = Decimal("8.125")
EXPLICIT_HIT = Decimal("0.65")


def read(path):
    return json.loads(path.read_text())


def stamp(value):
    return datetime.fromisoformat(value).timestamp()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def summarize(rows):
    assert len(rows) == len({r['id'] for r in rows}), 'Duplicate ledger request'
    values = Counter()
    unknown = Counter()
    for r in rows:
        if not r['usage_available']:
            assert r['status'] != 'success', 'Successful response missing usage'
            unknown[r['status']] += 1
            continue
        i, o, c = r['input_tokens'], r['output_tokens'], r['cached_input_tokens']
        assert type(c) is int and 0 <= c <= i and o >= 0
        assert r['total_tokens'] == i + o
        values.update(input_tokens=i, output_tokens=o, cached_input_tokens=c,
                      total_tokens=i+o, requests_with_usage=1)
        mode, created = r['_cache_mode'], r['_cache_created']
        assert mode in ('explicit','implicit') and 0 <= created <= i-c
        assert mode == 'explicit' or created == 0
        values[mode+'_cache_requests'] += 1
        values[mode+'_cached_input_tokens'] += c
        values['cache_creation_input_tokens'] += created
        if r['reasoning_tokens'] is not None:
            assert 0 <= r['reasoning_tokens'] <= o
            values['reasoning_tokens'] += r['reasoning_tokens']
        else:
            values['reasoning_unknown_requests'] += 1
    i, c, o = (values[k] for k in ('input_tokens', 'cached_input_tokens', 'output_tokens'))
    created = values['cache_creation_input_tokens']
    cost = ((i-c-created)*RATES[0] + values['implicit_cached_input_tokens']*RATES[1]
            + values['explicit_cached_input_tokens']*EXPLICIT_HIT
            + created*EXPLICIT_CREATE + o*RATES[2])/Decimal(1_000_000)
    return dict(**values, request_attempts=len(rows), requests_without_usage=sum(unknown.values()),
                without_usage_status_counts=dict(unknown),
                known_usage_list_cost_cny=str(cost), known_usage_list_cost_usd=str(cost/FX),
                ledger_rows_sha256=fingerprint(sorted(rows, key=lambda r:r['id'])))


def audit(root, source):
    # The SQLite backup API includes committed WAL data and gives a stable snapshot.
    original = sqlite3.connect(source.resolve().as_uri()+'?mode=ro', uri=True)
    snapshot = sqlite3.connect(':memory:')
    original.backup(snapshot)
    original.close()
    snapshot.row_factory = sqlite3.Row
    ledger = [dict(r) for r in snapshot.execute("SELECT * FROM requests WHERE model IN ('kimi-k2.7-code','openai/kimi-k2.7-code')")]
    snapshot.close()
    grouped = defaultdict(list)
    by_usage = defaultdict(list)
    for r in ledger:
        if r['stage'] == 'generate':
            grouped[r['task_id']].append(r)
            if r['usage_available']:
                by_usage[r['task_id'],r['input_tokens'],r['output_tokens'],r['cached_input_tokens']].append(r)
    all_selected = set()
    stages, selected, scores = {}, {}, {}
    run_root = root/'PhoenixRepair/validation_runs'
    for label, name in RUNS.items():
        directory = run_root/name
        tasks = read(directory/'progress.json')['tasks']
        expected = 8 if label == 'retry8' else 51
        assert len(tasks) == len({t['instance_id'] for t in tasks}) == expected
        stage_rows, provenance, scores[label] = [], [], {}
        response_count = 0
        for t in tasks:
            iid = t['instance_id']
            raw_path = directory/iid/'kimi_requests.jsonl'
            raw = [json.loads(line) for line in raw_path.read_text().splitlines()]
            assert raw, 'Missing successful response evidence'
            candidates = grouped[iid]
            matched = []
            matched_ids = set()
            response_matches = []
            for entry in raw:
                u = entry['usage']
                same_usage = by_usage[iid,u['prompt_tokens'],u['completion_tokens'],u.get('prompt_tokens_details',{}).get('cached_tokens')]
                matches = [r for r in same_usage if r['finished_at']
                           and stamp(r['started_at']) >= entry['time']-entry['seconds']-0.02
                           # Ledger timestamps precede the SQLite commit; slow
                           # I/O can delay the raw log by several seconds.
                           and -0.1 <= entry['time']-stamp(r['finished_at']) <= entry['seconds']+1]
                response_matches.append(matches)
                assert u['total_tokens'] == u['prompt_tokens']+u['completion_tokens']
            # Different samples can issue identical early requests concurrently.
            # Identify worker PIDs from independently unique responses first.
            identified_pids = {matches[0]['pid'] for matches in response_matches if len(matches)==1}
            assert identified_pids, 'No uniquely identified worker'
            for entry, matches in zip(raw, response_matches):
                matches = [r for r in matches if r['pid'] in identified_pids and r['id'] not in matched_ids]
                assert len(matches) == 1, f'Ambiguous or missing response-to-ledger match: {label}, {iid}, {entry["time"]}, matches={len(matches)}'
                details = entry['usage'].get('prompt_tokens_details',{})
                explicit = details.get('cache_type') == 'ephemeral' or 'cache_creation_input_tokens' in details
                created = details.get('cache_creation_input_tokens',0)
                if explicit:
                    assert details.get('cache_type') == 'ephemeral'
                    for alias in ('cache_write_tokens','cache_creation_tokens'):
                        assert details.get(alias,created) == created
                matches[0]['_cache_mode'] = 'explicit' if explicit else 'implicit'
                matches[0]['_cache_created'] = created
                matched.append(matches[0])
                matched_ids.add(matches[0]['id'])
            assert len(matched) == len({r['id'] for r in matched})
            pids = {r['pid'] for r in matched}
            start = min(stamp(t['started_at']), min(stamp(r['started_at']) for r in matched))
            end = stamp(t['finished_at'])
            rows = [r for r in candidates if r['pid'] in pids and start <= stamp(r['started_at']) <= end]
            assert {r['id'] for r in matched} <= {r['id'] for r in rows}
            assert {r['id'] for r in rows if r['usage_available']} == {r['id'] for r in matched}, 'Missing raw cache mode evidence'
            assert not all_selected.intersection(r['id'] for r in rows)
            all_selected.update(r['id'] for r in rows)
            stage_rows.extend(rows)
            response_count += len(raw)
            evaluation_path = root/'evaluation/results'/EVALUATIONS[label]/'tasks'/iid/'evaluation.json'
            e = read(evaluation_path)
            assert e['status'] in ('resolved', 'unresolved')
            assert e['resolved'] == (e['status'] == 'resolved')
            scores[label][iid] = e['resolved']
            provenance.append(dict(task=iid, response_sha256=hashlib.sha256(raw_path.read_bytes()).hexdigest(),
                                   evaluation_sha256=hashlib.sha256(evaluation_path.read_bytes()).hexdigest()))
        selected[label] = stage_rows
        stages[label] = dict(tasks=expected, passed=sum(scores[label].values()),
                             raw_success_responses_matched=response_count,
                             source_manifest_sha256=fingerprint(sorted(provenance,key=lambda x:x['task'])),
                             provider_usage=summarize(stage_rows))

    # Summary calls have no task_id. Verify saved completion times against the
    # successful cohort, then include all attempts of that dedicated worker.
    summary_paths = sorted((run_root/RUNS['final_original']/'summaries').glob('*/summary_result.json'))
    assert len(summary_paths) == 51
    summary_meta = [read(p) for p in summary_paths]
    candidates = [r for r in ledger if r['stage'] == 'patch_comparison']
    matched = {}
    for m in summary_meta:
        assert m['status'] == 'completed' and m['attempts'] == 1
        matches = [r for r in candidates if r['status'] == 'success' and r['finished_at']
                   and 0 <= stamp(m['finished_at'])-stamp(r['finished_at']) < 2]
        assert matches, 'Missing summary completion'
        matched.update({r['id']:r for r in matches})
    # Several summaries finish within the same second. Verify the entire
    # dedicated cohort, rather than inventing a per-task ledger association.
    assert len(matched) == 51
    pids = {r['pid'] for r in matched.values()}
    assert len(pids) == 1
    for m,r in zip(sorted(summary_meta,key=lambda m:m['finished_at']),
                   sorted(matched.values(),key=lambda r:r['finished_at'])):
        assert 0 <= stamp(m['finished_at'])-stamp(r['finished_at']) < 2
    day = min(m['finished_at'] for m in summary_meta)[:10]
    end = max(stamp(m['finished_at']) for m in summary_meta)
    summaries = [r for r in candidates if r['pid'] in pids and r['started_at'].startswith(day)
                 and stamp(r['started_at']) <= end]
    assert {r['id'] for r in summaries if r['status']=='success'} == set(matched)
    # The dedicated summary wrapper sends plain messages, without cache_control.
    for r in summaries:
        r['_cache_mode'], r['_cache_created'] = 'implicit', 0
    assert not all_selected.intersection(r['id'] for r in summaries)
    selected['summary'] = summaries
    stages['summary'] = dict(tasks=51, source_manifest_sha256=fingerprint(summary_meta),
                             provider_usage=summarize(summaries))
    base_ids = set(scores['sample1'])
    assert all(set(scores[k])==base_ids for k in ('sample2','sample3','final_original'))
    assert set(scores['retry8']) <= base_ids
    assert all(not scores['final_original'][iid] for iid in scores['retry8'])
    merged = dict(scores['final_original']); merged.update(scores['retry8'])
    rows = []
    for label, display in [('sample1','Sample 1'),('sample2','Sample 2'),('sample3','Sample 3')]:
        rows.append(dict(experiment=display,tasks=51,passed=sum(scores[label].values()),
                         stages=[label], provider_usage=summarize(selected[label])))
    tts_stages = ['sample1','sample2','sample3','summary','final_original','retry8']
    total_rows = [r for label in tts_stages for r in selected[label]]
    rows.append(dict(experiment='TTS Final',tasks=51,passed=sum(merged.values()),stages=tts_stages,
                     provider_usage=summarize(total_rows)))
    for row in rows:
        u=row['provider_usage']
        row['resolved_percent']=f"{100*row['passed']/row['tasks']:.2f}"
        row['per_task']={k:str(Decimal(u[k])/row['tasks']) for k in
                         ('input_tokens','cached_input_tokens','output_tokens','total_tokens',
                          'known_usage_list_cost_cny','known_usage_list_cost_usd')}
    return dict(model='kimi-k2.7-code',benchmark='SWE-bench Pro v2 HARD-51',
                audited_at=datetime.now(timezone.utc).isoformat(), rows=rows, stages=stages,
                final_score=dict(original_passed=stages['final_original']['passed'],
                                 retry_tasks=8,retry_passed=stages['retry8']['passed'],
                                 merged_passed=sum(merged.values()),total=51),
                pricing=dict(region='Beijing',currency='CNY',per_million_tokens=dict(
                    uncached_input='6.5',implicit_cached_input='1.3',explicit_cached_input='0.65',
                    explicit_cache_creation='8.125',output='27'), usd_cny_reference=str(FX),
                    source='https://help.aliyun.com/zh/model-studio/kimi-k2-7-code',
                    cache_source='https://help.aliyun.com/zh/model-studio/context-cache',checked_date='2026-11-08'),
                scope=[
                    'Retained sample1/2/3 and original final workers, all 51 summaries, and eight additional retries.',
                    'TTS Final includes original failed attempts plus all eight retry attempts; original summary reused without new summary calls.',
                    'All raw successful responses matched uniquely to ledger by task, completion time, input/output/cache counts; include other attempts in identified worker/time windows.',
                    'Missing usage on failed/pending requests remains unknown, not zero. Costs are known-usage public-price estimates, not invoices.',
                    'Input includes cache hits. Output includes reported reasoning tokens; reasoning is not added again.',
                    'Price explicit-cache responses using raw cache_type=ephemeral and cache_creation_input_tokens, including creation premium; ordinary cached_tokens-only responses and plain summary calls use implicit-cache pricing.',
                    'Excludes discarded workers, unrelated runs (including the separate 20261107 final-sample-2h experiment), server/evaluation costs.',
                    'Sample rows are included in TTS Final; do not add table rows together.',
                    'Eight retries use 7200-second generation budgets; earlier retained stages use original budgets. This is not a uniform-budget pass@1 result.',
                ])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True,help='Private TSE-Revision directory')
    parser.add_argument('--ledger',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=audit(args.root,args.ledger)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    with args.output.with_suffix('.csv').open('w',newline='') as f:
        fields=['experiment','tasks','passed','resolved_percent','input_tokens','cached_input_tokens',
                'output_tokens','total_tokens','request_attempts','requests_with_usage','requests_without_usage',
                'known_usage_list_cost_cny','known_usage_list_cost_usd']
        writer=csv.DictWriter(f,fieldnames=fields,lineterminator='\n');writer.writeheader()
        for row in result['rows']:
            data=dict(row,**row['provider_usage']);writer.writerow({k:data[k] for k in fields})
    print(json.dumps(result['rows'],ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
