"""Independent repair hypotheses, each completed into its own location candidate.

This enforces bookkeeping and exploration order, not semantic correctness. Evidence
is supplied from the issue/current checkout; no reference fix or evaluator is used.
"""
import copy

POLICY = 'independent_hypotheses_v1'


def text(value):
    return isinstance(value, str) and bool(value.strip())


def evidence(value):
    return isinstance(value, list) and bool(value) and all(text(x) for x in value)


def enabled(data):
    return data.get('exploration_policy') == POLICY


def state(data):
    investigation = data.get('investigation', {})
    hypotheses = investigation.get('hypotheses', [])
    return {'exploration_policy': POLICY,
            'active_hypothesis': investigation.get('active_hypothesis'),
            'hypotheses': [{k: h[k] for k in ('id', 'idea', 'status', 'candidate_id') if k in h}
                           for h in hypotheses],
            'open_hypotheses': [h['id'] for h in hypotheses if h['status'] == 'open'],
            'candidate_count': len(data['candidates'])}


def validate(data, *, allow_open=False):
    inv = data.get('investigation', {})
    hs = inv.get('hypotheses')
    if not isinstance(hs, list) or not hs:
        raise ValueError('First checkpoint independent repair hypotheses from the issue and shared source facts')
    ids = set()
    for h in hs:
        if not isinstance(h, dict) or not text(h.get('id')) or h['id'] in ids:
            raise ValueError('Hypotheses need unique non-empty ids')
        ids.add(h['id'])
        if not text(h.get('idea')) or not evidence(h.get('grounding')):
            raise ValueError(f'{h["id"]}: supply idea and grounding (issue/current-source facts)')
        if h.get('status') not in ('open', 'retained', 'rejected'):
            raise ValueError(f'{h["id"]}: status must be open, retained or rejected; omissions are not rejections')
        if h['status'] == 'rejected' and (not text(h.get('reason')) or not evidence(h.get('rejection_evidence'))):
            raise ValueError(f'{h["id"]}: rejection needs reason and concrete rejection_evidence; uncertainty is not disproof')
    if len(hs) == 1 and not text(inv.get('single_hypothesis_reason')):
        raise ValueError('If only one grounded hypothesis exists, supply single_hypothesis_reason; do not invent a second')
    by_h = {h['id']: h for h in hs}
    by_c = {}
    owners = set()
    for c in data['candidates']:
        hid = c.get('hypothesis_id')
        if hid not in by_h or hid in owners or c['id'] in by_c:
            raise ValueError('Each complete candidate must belong to a distinct registered hypothesis')
        h = by_h[hid]
        if h['status'] != 'retained' or h.get('candidate_id') != c['id']:
            raise ValueError('Saved candidates must match their retained hypothesis and candidate_id')
        owners.add(hid); by_c[c['id']] = c
    for h in hs:
        if h['status'] == 'retained' and (h.get('candidate_id') not in by_c or
                by_c[h['candidate_id']].get('hypothesis_id') != h['id']):
            raise ValueError(f'{h["id"]}: save its complete candidate before marking it retained')
    if inv.get('active_hypothesis') is not None and inv['active_hypothesis'] not in by_h:
        raise ValueError('active_hypothesis must refer to a registered hypothesis')
    pending = [h['id'] for h in hs if h['status'] == 'open']
    if pending and not allow_open:
        raise ValueError('Unfinished hypotheses remain: ' + ', '.join(pending) +
                         '; explore each, or reject with concrete evidence. One saved candidate is not a stopping rule.')


def checkpoint(data, record, control):
    inv = copy.deepcopy(data.get('investigation', {}))
    old = {h['id']: h for h in inv.get('hypotheses', [])}
    updates = record.get('hypotheses', [])
    if not isinstance(updates, list):
        raise ValueError('hypotheses must be a list; updates merge by id and never silently remove branches')
    seen = set()
    for update in updates:
        hid = update.get('id') if isinstance(update, dict) else None
        if not text(hid) or hid in seen:
            raise ValueError('Hypothesis updates need distinct ids')
        seen.add(hid)
        if hid not in old:
            if control.get('phase') in ('consolidate', 'submit'):
                raise ValueError('New hypotheses are closed in the consolidation/submission budget')
            old[hid] = {'id': hid, 'status': 'open'}
        elif 'idea' in update and update['idea'] != old[hid]['idea']:
            if old[hid]['status'] != 'open' or not evidence(update.get('grounding')):
                raise ValueError('Preserve the original independent idea unless an OPEN branch is refined with '
                                 'explicit current-source grounding. Omit idea in routine progress checkpoints; '
                                 'register a new id for a different mechanism or a retained branch.')
            # Exploration must be able to correct/refine an initial mechanism.
            # Keep the original and every grounded revision rather than forcing
            # byte-identical prose or silently overwriting independent branches.
            old[hid].setdefault('original_idea', old[hid]['idea'])
            old[hid].setdefault('idea_revisions', []).append({
                'previous': old[hid]['idea'], 'refined': update['idea'],
                'grounding': copy.deepcopy(update['grounding']), 'notes': record.get('notes', '')})
        old[hid] = {**old[hid], **copy.deepcopy(update)}
    inv.update({k: copy.deepcopy(v) for k, v in record.items() if k not in ('hypotheses', 'operation')})
    inv['hypotheses'] = list(old.values())
    data['investigation'] = inv
    validate(data, allow_open=True)
    pending = [h['id'] for h in old.values() if h['status'] == 'open']
    active = inv.get('active_hypothesis')
    if pending and active not in pending and control.get('phase') not in ('consolidate', 'submit'):
        raise ValueError('Choose an open active_hypothesis instead of refining a completed branch')
    if control.get('switch_required') and pending and active == control.get('active_hypothesis'):
        raise ValueError('Switch to another open hypothesis; this branch has used its current exploration window')
    if record.get('next_action') == 'finalize' and pending and control.get('phase') not in ('consolidate', 'submit'):
        raise ValueError('Cannot finalize while independent hypotheses remain open within the exploration budget')
    data.setdefault('hypothesis_history', []).append(copy.deepcopy(record))


def bind_candidate(data, candidate, control):
    validate(data, allow_open=True)
    inv = data['investigation']
    hid = candidate.get('hypothesis_id')
    hs = {h['id']: h for h in inv['hypotheses']}
    if hid not in hs or hid != inv.get('active_hypothesis'):
        raise ValueError('Select this registered hypothesis as active before completing its own candidate: '
                         f'candidate.hypothesis_id={hid!r}, active_hypothesis={inv.get("active_hypothesis")!r}. '
                         'Use location_checkpoint with notes and active_hypothesis set to the intended open id; '
                         'do not resubmit the candidate until the checkpoint succeeds.')
    h = hs[hid]
    if h['status'] == 'rejected':
        raise ValueError('Cannot save a rejected hypothesis; explicitly reopen it with new evidence first')
    if h['status'] == 'retained' and any(x['status'] == 'open' for x in hs.values()) and control.get('phase') not in ('consolidate', 'submit'):
        raise ValueError('Complete the other open hypotheses before refining this retained candidate')
    previous = next((c for c in data['candidates'] if c['id'] == candidate['id']), None)
    if previous and previous['hypothesis_id'] != hid:
        raise ValueError('Do not overwrite another hypothesis candidate; use a distinct candidate id')
    if h.get('candidate_id') and h['candidate_id'] != candidate['id']:
        raise ValueError('Revise the same hypothesis using its existing candidate id')
    candidate['hypothesis'] = h['idea']
    h.update(status='retained', candidate_id=candidate['id'])


def remove_candidate(data, candidate, request):
    if not evidence(request.get('rejection_evidence')):
        raise ValueError('Removing a branch requires concrete rejection_evidence as well as reason')
    h = next(h for h in data['investigation']['hypotheses'] if h['id'] == candidate['hypothesis_id'])
    h.update(status='rejected', reason=request['reason'], rejection_evidence=copy.deepcopy(request['rejection_evidence']))
    h.pop('candidate_id', None)


def finalize(data, control=None):
    # Finalized artifacts carry the genuine budget outcome for host-side rereads.
    budget = ((control or {}).get('phase') in ('consolidate', 'submit') or
              (data.get('finalized') and data.get('exploration_outcome') == 'budget_limited'))
    validate(data, allow_open=bool(budget))
    pending = state(data)['open_hypotheses']
    return {'exploration_policy': POLICY,
            'investigation': copy.deepcopy(data['investigation']),
            'hypothesis_history': copy.deepcopy(data.get('hypothesis_history', [])),
            'exploration_outcome': 'budget_limited' if pending else 'all_registered_hypotheses_processed',
            'unfinished_hypotheses': pending}
