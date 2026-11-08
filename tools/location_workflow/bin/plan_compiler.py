"""Deterministic localization-plan compiler. No model calls or source execution."""
import copy

POLICY = 'compiled_strategies_v3'
SCHEMA = 'location_candidate_v3'
DERIVED_FIELDS = ('hypothesis', 'files_to_modify', 'unresolved_questions',
                  'requirement_coverage', 'location_choices')


def text(value):
    return isinstance(value, str) and bool(value.strip())


def keys(obj, allowed, label):
    if not isinstance(obj, dict) or set(obj) - set(allowed):
        raise ValueError(f'{label} accepts only: {", ".join(allowed)}')


def references(value, known, label, *, empty=False):
    if (not isinstance(value, list) or not all(text(v) for v in value)
            or len(set(value)) != len(value) or set(value) - set(known) or (not value and not empty)):
        raise ValueError(f'{label} must reference existing change IDs: {", ".join(known)}')
    return value


def compile_plan(plan, requirements, read_source, *, dynamic=False):
    """All final descriptions, source locations and checks derive from this one plan."""
    keys(plan, ('changes', 'checks', 'choices', 'unchanged_requirements'), 'plan')
    changes = plan.get('changes')
    if not isinstance(changes, list) or not changes:
        raise ValueError('plan.changes must contain the complete set of cooperating edits')
    if not isinstance(requirements, list) or not requirements or not all(map(text, requirements)):
        raise ValueError('Record issue requirements before compiling a plan')
    ids, anchors, files, bindings, coverage = {}, {}, {}, {}, {i: [] for i in range(1, len(requirements)+1)}
    for c in changes:
        keys(c, ('id', 'intent', 'file_path', 'start_line', 'end_line', 'requirements', 'edit_type'), 'change')
        cid = c.get('id')
        if not text(cid) or cid in ids or not text(c.get('intent')) or not text(c.get('file_path')):
            raise ValueError('Each change needs unique id, intent, file_path and source range')
        start, end = c.get('start_line'), c.get('end_line')
        if type(start) is not int or type(end) is not int or not 1 <= start <= end:
            raise ValueError(f'Change {cid} needs integer start_line/end_line')
        if c.get('edit_type', 'modify') not in ('modify', 'insert_before', 'insert_after'):
            raise ValueError('edit_type must be modify/insert_before/insert_after')
        refs = c.get('requirements')
        if (not isinstance(refs, list) or any(type(i) is not int or i not in coverage for i in refs)
                or len(refs) != len(set(refs))):
            raise ValueError(f'Change {cid} needs requirements as unique 1-based requirement indices')
        anchor = (c['file_path'], start, end)
        if anchor not in anchors:
            code = read_source(*anchor)
            if not text(code):
                raise ValueError(f'Change {cid} must have a non-empty current-source anchor')
            mod = {'id': f'L{len(anchors)+1}', 'location': {'start_line': start, 'end_line': end},
                   'current_code': code, 'reason': '', 'change_ids': []}
            anchors[anchor] = mod
            files.setdefault(c['file_path'], []).append(mod)
        mod = anchors[anchor]
        mod['change_ids'].append(cid)
        mod['reason'] += ('; ' if mod['reason'] else '') + f'{cid}: {c["intent"]}'
        bindings[cid] = mod['id']
        ids[cid] = c
        for index in refs:
            coverage[index].append(cid)

    def locations(change_ids):
        return list(dict.fromkeys(bindings[cid] for cid in change_ids))

    choices = plan.get('choices', {})
    if not isinstance(choices, dict):
        raise ValueError('plan.choices must map decision IDs to option_id and change_ids')
    choice_output, justified = {}, {cid for cids in coverage.values() for cid in cids}
    for did, choice in choices.items():
        if not text(did):
            raise ValueError('Decision IDs must be non-empty strings')
        keys(choice, ('option_id', 'change_ids', 'unchanged_reason'), 'choice')
        if not text(choice.get('option_id')):
            raise ValueError(f'Choice {did} needs option_id')
        cids = references(choice.get('change_ids'), ids, f'Choice {did}', empty=True)
        if not cids and not text(choice.get('unchanged_reason')):
            raise ValueError(f'Choice {did} without edits needs unchanged_reason with evidence')
        if cids and 'unchanged_reason' in choice:
            raise ValueError(f'Choice {did} cannot declare edits and unchanged_reason together')
        justified.update(cids)
        choice_output[did] = {'option_id': choice['option_id'], 'location_ids': locations(cids),
                             'explanation': 'Implemented by ' + ', '.join(cids) if cids else choice['unchanged_reason']}
    if set(ids) - justified:
        raise ValueError('Changes lack a requirement or selected option: ' + ', '.join(sorted(set(ids)-justified)))
    unchanged = plan.get('unchanged_requirements', {})
    if not isinstance(unchanged, dict) or any(k not in {str(i) for i in coverage} or not text(v) for k, v in unchanged.items()):
        raise ValueError('unchanged_requirements maps requirement index strings to existing-behavior evidence')
    covered_output = []
    for index, cids in coverage.items():
        if bool(cids) == (str(index) in unchanged):
            raise ValueError(f'Requirement {index}: supply edits OR unchanged evidence, exactly one')
        covered_output.append({'requirement_index': index, 'location_ids': locations(cids),
                               'explanation': 'Implemented by ' + ', '.join(cids) if cids else unchanged[str(index)]})
    checks = plan.get('checks', [])
    if not isinstance(checks, list):
        raise ValueError('plan.checks must be a list')
    questions, qids = [], set()
    for q in checks:
        keys(q, ('id', 'change_ids', 'kind', 'condition', 'expected'), 'check')
        qid = q.get('id')
        if not text(qid) or qid in qids or q.get('kind') not in ('behavior', 'edge_case', 'compatibility'):
            raise ValueError('Each check needs unique id and kind behavior/edge_case/compatibility; '
                             'location decisions are not implementation checks')
        cids = references(q.get('change_ids'), ids, f'Check {qid}')
        if not text(q.get('condition')) or not text(q.get('expected')):
            raise ValueError(f'Check {qid} needs condition and expected behavior')
        qids.add(qid)
        questions.append({'id': qid, 'scope': 'implementation', 'kind': q['kind'],
                          'change_ids': list(cids), 'location_ids': locations(cids),
                          'question': f'For {", ".join(cids)}, when {q["condition"]}, verify: {q["expected"]}'})
    description = ['Selected complete repair plan:']
    for cid, c in ids.items():
        description.append(f'- {cid} [{c["file_path"]}:{c["start_line"]}-{c["end_line"]}]: '
                           f'{c["intent"]} (edit_type={c.get("edit_type", "modify")})')
    for did, choice in choices.items():
        description.append(f'- Choice {did}={choice["option_id"]}: {choice_output[did]["explanation"]}')
    return {('repair_plan' if dynamic else 'hypothesis'): '\n'.join(description),
            'files_to_modify': [{'file_path': path, 'modifications': mods} for path, mods in files.items()],
            'unresolved_questions': questions, 'requirement_coverage': covered_output,
            'location_choices': choice_output}


def validate_compilation(candidate):
    """Reject hand-edited derived fields, even if their JSON shape is valid."""
    sources = {}
    for f in candidate.get('files_to_modify', []):
        for mod in f.get('modifications', []):
            loc = mod['location'];key = (f['file_path'], loc['start_line'], loc['end_line'])
            if key in sources:
                raise ValueError('Compiled locations must not duplicate source anchors')
            sources[key] = mod['current_code']
    def source(*key):
        if key not in sources:
            raise ValueError(f'Plan commitment has no source location: {key}')
        return sources[key]
    dynamic = candidate.get('schema_version') == 'location_candidate_v4'
    derived = compile_plan(candidate.get('plan'), candidate.get('requirements'), source, dynamic=dynamic)
    for key in derived:
        if candidate.get(key) != derived[key]:
            raise ValueError(f'{key} disagrees with the compiled plan; revise the plan, not derived fields')
    context, excerpts = candidate.get('context', []), candidate.get('review_context', [])
    if not isinstance(context, list) or not isinstance(excerpts, list) or len(context) != len(excerpts):
        raise ValueError('Read-only review context does not match its source references')
    for ref, excerpt in zip(context, excerpts):
        keys(ref, ('file_path', 'start_line', 'end_line'), 'read-only context')
        if ({k: excerpt.get(k) for k in ref} != ref or not text(excerpt.get('current_code'))):
            raise ValueError('Read-only context must preserve its exact source reference and code')


def revise_plan(plan, request):
    """Apply small, explicit deltas atomically; dangling references fail compilation."""
    result = copy.deepcopy(plan)
    for field in ('changes', 'checks'):
        rows = {item['id']: item for item in result.get(field, [])}
        removed = request.get('remove_' + field, [])
        if not isinstance(removed, list) or any(not text(cid) or cid not in rows for cid in removed):
            raise ValueError(f'remove_{field} must name existing IDs')
        for cid in removed:
            del rows[cid]
        updates = request.get('upsert_' + field, [])
        if not isinstance(updates, list):
            raise ValueError(f'upsert_{field} must be a list')
        seen = set()
        for item in updates:
            if not isinstance(item, dict) or not text(item.get('id')) or item['id'] in seen:
                raise ValueError(f'upsert_{field} needs unique item IDs')
            seen.add(item['id'])
            rows[item['id']] = {**rows.get(item['id'], {}), **item}
        result[field] = list(rows.values())
    if 'choices' in request:
        if not isinstance(request['choices'], dict):
            raise ValueError('choices must be an object')
        result['choices'] = {**result.get('choices', {}), **request['choices']}
    if 'unchanged_requirements' in request:
        result['unchanged_requirements'] = request['unchanged_requirements']
    return result
