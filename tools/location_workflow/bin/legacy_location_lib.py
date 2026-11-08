"""Read-only source access and atomic localization artifacts; no model or shell calls."""
import hashlib
import copy
import json
import os
import re
import runpy
import sys
from functools import lru_cache
from pathlib import Path

EXCLUDED = {'.git', '.hg', '.svn', '__pycache__', '.venv', 'node_modules', '.pytest_cache'}
STATUSES = {'exploring', 'plausible', 'supported', 'rejected', 'unresolved'}
RETENTION_POLICY = 'retain_unrefuted_v1'
CONSISTENCY_POLICY = 'consistent_strategies_v2'
CANDIDATE_SCHEMA = 'location_candidate_v2'
STRICT_POLICY = 'compiled_strategies_v3'
STRICT_SCHEMA = 'location_candidate_v3'
DYNAMIC_POLICY = 'dynamic_hypotheses_v4'
DYNAMIC_SCHEMA = 'location_candidate_v4'


@lru_cache(maxsize=1)
def compiler():
    return runpy.run_path(str(Path(__file__).with_name('plan_compiler.py')))


def candidate_fingerprint(candidate):
    fields = ('id', 'plan', 'requirements', 'evidence', 'review_context', 'selected_location_choices', *compiler()['DERIVED_FIELDS'])
    if candidate.get('schema_version') == DYNAMIC_SCHEMA:
        fields += ('schema_version', 'hypothesis_id', 'hypothesis_versions', 'hypothesis_snapshots', 'repair_plan')
    body = {key: candidate.get(key) for key in fields}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def review_targets(candidate):
    return ({('change', c['id']) for c in candidate['plan']['changes']}
            | {('check', q['id']) for q in candidate['plan'].get('checks', [])}
            | {('requirement', str(i)) for i in range(1, len(candidate['requirements'])+1)}
            | {('choice', did) for did in candidate['location_choices']}
            | ({('hypothesis', hid) for hid in hypothesis_ids(candidate)}
               if candidate.get('schema_version') == DYNAMIC_SCHEMA else set()))


def validate_assessments(candidate, assessments):
    if not isinstance(assessments, list):
        raise ValueError('assessments must review every change, check, requirement and selected choice')
    seen = set()
    for a in assessments:
        if not isinstance(a, dict):
            raise ValueError('Each assessment must be an object')
        kind, identifier = a.get('type'), a.get('id')
        if not nonempty(kind) or not nonempty(identifier) or (kind, identifier) in seen:
            raise ValueError('Each assessment needs unique type/id strings')
        if a.get('verdict') not in ('consistent', 'inconsistent', 'uncertain'):
            raise ValueError('Review verdict must be consistent/inconsistent/uncertain')
        if not nonempty(a.get('source_evidence')) or not nonempty(a.get('reason')):
            raise ValueError('Semantic assessments need actual source evidence and reasoning, not just an approval flag')
        if kind == 'change' and a['verdict'] == 'consistent':
            change = next((c for c in candidate['plan']['changes'] if c['id'] == identifier), None)
            anchor = a.get('anchor_evidence')
            if change is None or not isinstance(anchor, dict):
                raise ValueError('A consistent change assessment needs anchor_evidence: file_path/start_line/end_line/quote')
            start, end = anchor.get('start_line'), anchor.get('end_line')
            if (anchor.get('file_path') != change['file_path'] or type(start) is not int or type(end) is not int
                    or not change['start_line'] <= start <= end <= change['end_line']):
                raise ValueError(f'{identifier}: approval evidence must be INSIDE the declared change range; '
                                 'neighboring/read-only context does not authorize an edit')
            source = next(m['current_code'] for f in candidate['files_to_modify'] if f['file_path'] == change['file_path']
                          for m in f['modifications'] if identifier in m.get('change_ids', []))
            lines = source.splitlines()[start-change['start_line']:end-change['start_line']+1]
            if not nonempty(anchor.get('quote')) or anchor['quote'].splitlines() != lines:
                raise ValueError(f'{identifier}: anchor_evidence.quote must copy the exact cited current source lines')
        seen.add((kind, identifier))
    expected = review_targets(candidate)
    if seen != expected:
        raise ValueError(f'Review target mismatch: missing={sorted(expected-seen)}, extra={sorted(seen-expected)}')


def validate_semantic_review(candidate):
    review = candidate.get('semantic_review', {})
    if review.get('fingerprint') != candidate_fingerprint(candidate):
        raise ValueError(f'Candidate {candidate["id"]} needs a current semantic review; call location_candidate operation=review')
    validate_assessments(candidate, review.get('assessments'))
    if review.get('status') != 'passed' or any(a['verdict'] != 'consistent' for a in review['assessments']):
        raise ValueError(f'Candidate {candidate["id"]} has semantic inconsistencies/insufficient evidence; inspect review findings and revise the plan')


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def decision_options(record):
    """Explicit location choices; each possible option must survive in a full plan."""
    decisions = record.get('location_decisions')
    if not isinstance(decisions, list):
        raise ValueError('location_decisions must be a list (use [] only when no location choices remain)')
    result = {}
    for d in decisions:
        if not isinstance(d, dict) or not nonempty(d.get('id')) or d['id'] in result or not nonempty(d.get('question')):
            raise ValueError('Each location decision needs a unique id and question')
        options = d.get('options')
        if not isinstance(options, list) or len(options) < 2:
            raise ValueError(f'Decision {d["id"]} needs at least two explicit options')
        indexed = {}
        for option in options:
            if not isinstance(option, dict) or not nonempty(option.get('id')) or option['id'] in indexed:
                raise ValueError('Decision option IDs must be unique non-empty strings')
            if not nonempty(option.get('description')) or option.get('status') not in ('possible', 'rejected'):
                raise ValueError('Each option needs description and status possible/rejected')
            if not isinstance(option.get('evidence'), list) or not option['evidence'] or not all(map(nonempty, option['evidence'])):
                raise ValueError('Each location option needs source/requirement evidence')
            validate_rejection(option)
            indexed[option['id']] = option
        result[d['id']] = indexed
    return result


def selected_choices(record, choices):
    options = decision_options(record)
    open_ids = {did for did, opts in options.items() if any(o['status'] == 'possible' for o in opts.values())}
    if not isinstance(choices, dict) or set(choices) != open_ids:
        raise ValueError('location_choices must choose exactly one option for EACH open location_decision')
    result = []
    for decision in record['location_decisions']:
        if decision['id'] not in open_ids:
            continue  # Every option explicitly refuted; retain its evidence in the audit only.
        did = decision['id']; chosen = choices[did]
        if not isinstance(chosen, dict):
            raise ValueError('Each location_choices value needs option_id, location_ids and explanation')
        oid = chosen.get('option_id')
        option = options[did].get(oid) if isinstance(oid, str) else None
        if option is None or option['status'] != 'possible':
            raise ValueError(f'Choose a possible option for {did}; unknown/rejected option {oid!r}')
        result.append({'decision_id': did, 'option_id': oid,
                       'question': decision['question'], 'choice': option['description'],
                       'location_ids': chosen.get('location_ids'), 'explanation': chosen.get('explanation')})
    return result


def validate_candidate_contract(candidate):
    """Validate a self-contained final strategy, including every declared change/question."""
    if candidate.get('schema_version') not in (CANDIDATE_SCHEMA, STRICT_SCHEMA, DYNAMIC_SCHEMA):
        raise ValueError('Expected location_candidate_v2; re-save this candidate with location_candidate')
    if candidate.get('schema_version') in (STRICT_SCHEMA, DYNAMIC_SCHEMA):
        compiler()['validate_compilation'](candidate)
    if not nonempty(candidate.get('id')) or not nonempty(candidate.get('hypothesis')):
        raise ValueError('Candidate needs id and one selected-strategy hypothesis')
    if not isinstance(candidate.get('evidence'), list) or not candidate['evidence'] or not all(map(nonempty, candidate['evidence'])):
        raise ValueError('Candidate needs supporting evidence')
    versions = candidate.get('hypothesis_versions')
    if not isinstance(versions, dict) or set(versions) != hypothesis_ids(candidate) or not all(map(nonempty, versions.values())):
        raise ValueError('Candidate needs version snapshots for all associated hypotheses')
    if candidate.get('schema_version') == DYNAMIC_SCHEMA:
        snapshots = candidate.get('hypothesis_snapshots')
        if not isinstance(snapshots, dict) or set(snapshots) != hypothesis_ids(candidate):
            raise ValueError('Candidate needs latest hypothesis snapshots for every linked hypothesis')
        for hid, snapshot in snapshots.items():
            if (not isinstance(snapshot, dict) or set(snapshot) != {'hypothesis', 'rationale', 'questions'}
                    or not nonempty(snapshot['hypothesis']) or not nonempty(snapshot['rationale'])
                    or not isinstance(snapshot['questions'], list)
                    or not all(isinstance(q, str) for q in snapshot['questions'])):
                raise ValueError('Invalid hypothesis snapshot')
            digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if digest != versions[hid]:
                raise ValueError('Hypothesis snapshot disagrees with its version')
        if candidate['hypothesis'] != snapshots[candidate['hypothesis_id']]['hypothesis']:
            raise ValueError('Candidate hypothesis must match its latest primary hypothesis snapshot')
    location_ids = set()
    files = candidate.get('files_to_modify')
    if not isinstance(files, list) or not files:
        raise ValueError('Candidate needs a complete files_to_modify list')
    for f in files:
        if not isinstance(f.get('modifications'), list) or not f['modifications']:
            raise ValueError('Each file needs modifications')
        for mod in f['modifications']:
            lid = mod.get('id')
            if not nonempty(lid) or lid in location_ids or not nonempty(mod.get('reason')):
                raise ValueError('Each modification needs a unique id and reason describing its intended change')
            location_ids.add(lid)

    def refs(value, allow_empty=False):
        if (not isinstance(value, list) or not all(nonempty(x) for x in value)
                or len(set(value)) != len(value) or set(value) - location_ids or (not value and not allow_empty)):
            raise ValueError('location_ids must reference existing modification IDs in THIS candidate')

    requirements = candidate.get('requirements')
    if not isinstance(requirements, list) or not requirements or not all(map(nonempty, requirements)):
        raise ValueError('Candidate needs its requirement snapshot')
    coverage = candidate.get('requirement_coverage')
    if not isinstance(coverage, list):
        raise ValueError('requirement_coverage must cover every requirement (1-based requirement_index)')
    covered = set()
    justified_locations = set()
    for item in coverage:
        index = item.get('requirement_index')
        if type(index) is not int or index not in range(1, len(requirements)+1) or index in covered:
            raise ValueError('requirement_coverage needs unique valid 1-based requirement_index values')
        refs(item.get('location_ids'), allow_empty=True)
        justified_locations.update(item['location_ids'])
        if not nonempty(item.get('explanation')):
            raise ValueError('Explain how each requirement is covered; empty locations require evidence it already holds')
        covered.add(index)
    if covered != set(range(1, len(requirements)+1)):
        raise ValueError('Incomplete repair plan: requirement_coverage omits issue requirements')
    questions = candidate.get('unresolved_questions')
    if not isinstance(questions, list):
        raise ValueError('unresolved_questions must be a list')
    for q in questions:
        if not isinstance(q, dict) or not nonempty(q.get('question')) or q.get('scope') != 'implementation':
            raise ValueError('Each unresolved question needs question, scope="implementation", location_ids. '
                             'Questions changing files/locations belong in location_decisions and separate candidates.')
        refs(q.get('location_ids'))
    choices = candidate.get('location_choices')
    selected = candidate.get('selected_location_choices')
    if not isinstance(choices, dict) or not isinstance(selected, list):
        raise ValueError('Candidate needs location_choices and selected_location_choices')
    for chosen in choices.values():
        if not isinstance(chosen, dict) or not nonempty(chosen.get('option_id')) or not nonempty(chosen.get('explanation')):
            raise ValueError('Each chosen option needs option_id, location_ids and explanation')
        refs(chosen.get('location_ids'), allow_empty=True)
        justified_locations.update(chosen['location_ids'])
    if (len(selected) != len(choices) or any(not isinstance(s, dict) or not nonempty(s.get('question'))
            or not nonempty(s.get('choice')) for s in selected)
            or {s.get('decision_id'): {key: s.get(key) for key in ('option_id', 'location_ids', 'explanation')}
                for s in selected} != choices):
        raise ValueError('Selected location choices do not match the chosen options')
    if location_ids - justified_locations:
        raise ValueError('Each modification must support a requirement or selected location choice: '
                         + ', '.join(sorted(location_ids - justified_locations)))


def finalize_analysis(data):
    """Publish candidate descriptions once; keep exploratory prose in a separate artifact."""
    if data.get('candidate_policy') not in (CONSISTENCY_POLICY, STRICT_POLICY, DYNAMIC_POLICY):
        return data
    validate_retention(data)
    result = copy.deepcopy(data)
    hypotheses = []
    for h in data['investigation']['hypotheses']:
        entry = {key: h[key] for key in ('id', 'status', 'source_version')}
        entry['candidate_ids'] = [c['id'] for c in data['candidates'] if h['id'] in hypothesis_ids(c)]
        if h['status'] == 'rejected':
            entry.update({key: h[key] for key in ('hypothesis', 'rationale', 'rejection_evidence')})
        hypotheses.append(entry)
    result['investigation'] = {key: copy.deepcopy(data['investigation'][key]) for key in
        ('requirements', 'confirmed_facts', 'location_decisions', 'location_scope', 'scope_evidence')
        if key in data['investigation']}
    result['investigation']['hypotheses'] = hypotheses
    result.pop('hypothesis_history', None)
    result['finalized'] = True
    return result


def hypothesis_ids(candidate):
    """Several strategies may share exactly the same complete location set."""
    primary = candidate.get('hypothesis_id')
    additional = candidate.get('hypothesis_ids', [])
    if not isinstance(primary, str) or not primary.strip():
        raise ValueError('candidate needs hypothesis_id')
    if not isinstance(additional, list) or not all(isinstance(x, str) and x.strip() for x in additional):
        raise ValueError('hypothesis_ids must be a string list')
    return {primary, *additional}


def validate_rejection(hypothesis):
    if hypothesis.get('status') == 'rejected':
        evidence = hypothesis.get('rejection_evidence')
        if not isinstance(evidence, list) or not evidence or not all(isinstance(x, str) and x.strip() for x in evidence):
            raise ValueError('rejected requires rejection_evidence: concrete source/requirement contradictions '
                             'showing this strategy cannot work, not lower confidence or a preferred alternative')


def validate_retention(data, *, require_review=True):
    """Check declared strategy coverage, not the semantic truth of model evidence."""
    if data.get('candidate_policy') not in (RETENTION_POLICY, CONSISTENCY_POLICY, STRICT_POLICY, DYNAMIC_POLICY):
        return  # Previously saved artifacts remain readable.
    hypotheses = data.get('investigation', {}).get('hypotheses', [])
    by_id = {h['id']: h for h in hypotheses}
    if not by_id or len(by_id) != len(hypotheses):
        raise ValueError('Retained candidates need unique recorded hypotheses')
    for h in hypotheses:
        if h.get('status') not in STATUSES:
            raise ValueError('Invalid hypothesis status')
        validate_rejection(h)
    covered = set()
    for candidate in data.get('candidates', []):
        refs = hypothesis_ids(candidate)
        if any(ref not in by_id or by_id[ref]['status'] == 'rejected' for ref in refs):
            raise ValueError('Candidate references a missing or rejected hypothesis')
        covered.update(refs)
    missing = {h['id'] for h in hypotheses if h['status'] != 'rejected'} - covered
    if missing:
        raise ValueError('Unrefuted strategies lack complete candidates: ' + ', '.join(sorted(missing)) +
                         '. Save their full location sets; merge only identical sets using hypothesis_ids. '
                         'Uncertainty or budget pressure is not rejection evidence.')
    if data.get('candidate_policy') in (CONSISTENCY_POLICY, STRICT_POLICY, DYNAMIC_POLICY):
        record = data['investigation']
        options = decision_options(record)
        candidate_ids = [c.get('id') for c in data.get('candidates', [])]
        if any(not nonempty(cid) for cid in candidate_ids) or len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError('Each candidate needs a unique non-empty id')
        used = set()
        for c in data.get('candidates', []):
            expected_schema = {STRICT_POLICY: STRICT_SCHEMA, DYNAMIC_POLICY: DYNAMIC_SCHEMA}.get(data['candidate_policy'])
            if expected_schema and c.get('schema_version') != expected_schema:
                raise ValueError('Compile each candidate from a plan using location_candidate')
            validate_candidate_contract(c)
            if c['requirements'] != record['requirements']:
                raise ValueError(f'Candidate {c["id"]} has stale requirements; review and re-save it')
            expected_versions = {ref: by_id[ref].get('source_version') for ref in hypothesis_ids(c)}
            if c.get('hypothesis_versions') != expected_versions or not all(expected_versions.values()):
                raise ValueError(f'Candidate {c["id"]} has stale hypothesis notes; reconcile its strategy/locations/questions and re-save')
            selected = selected_choices(record, c['location_choices'])
            if c['selected_location_choices'] != selected:
                raise ValueError(f'Candidate {c["id"]} has stale location choices; review and re-save')
            used.update((did, chosen['option_id']) for did, chosen in c['location_choices'].items())
        missing_options = [(did, oid) for did, opts in options.items() for oid, opt in opts.items()
                           if opt['status'] == 'possible' and (did, oid) not in used]
        if missing_options:
            raise ValueError(f'Possible location options lack complete candidates: {missing_options}. '
                             'Save each viable full strategy; do not leave alternatives only as questions.')
        if data['candidate_policy'] in (STRICT_POLICY, DYNAMIC_POLICY) and require_review:
            for c in data['candidates']:
                validate_semantic_review(c)


def decode_request(raw):
    """Accept redundant request wrappers and unescaped regex slashes, never eval code."""
    normalized = False
    def parse(text):
        nonlocal normalized
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            if 'Invalid \\escape' not in exc.msg:
                raise
            # Preserve regex escapes such as \\. or \\s literally; leave valid
            # JSON escapes and all other malformed JSON to the normal validator.
            repaired = re.sub(r'\\(?!["\\/bfnrtu])', lambda _: '\\\\', text)
            result = json.loads(repaired)
            normalized = True
            return result
    value = parse(raw)
    for _ in range(3):
        if isinstance(value, dict) and set(value) == {'request'}:
            value = value['request']
            if isinstance(value, str):
                value = parse(value)
            normalized = True
        else:
            break
    if not isinstance(value, dict):
        raise ValueError('Request must be a JSON object')
    return value, normalized


class Store:
    def __init__(self, root, artifacts='/root', *, policy=DYNAMIC_POLICY):
        self.root = Path(root).resolve()
        self.artifacts = Path(artifacts)
        self.path = self.artifacts / 'analysis.json'
        self.policy = policy

    def load(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {'investigation': {}, 'candidates': []}

    def write(self, data):
        self.artifacts.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        tmp.replace(self.path)

    def control(self):
        p = self.artifacts / 'location_control.json'
        return json.loads(p.read_text()) if p.exists() else {'phase': 'map'}

    def source(self, name, file=False):
        p = Path(name)
        if p.is_absolute() or '..' in p.parts or any(x in EXCLUDED for x in p.parts):
            raise ValueError('Use a repository-relative current-source path; metadata is forbidden')
        p = self.root / p
        # Reject symlinks even if currently inside root: no indirect metadata access.
        if any(q.is_symlink() for q in [p, *p.parents] if q != self.root and self.root in q.parents):
            raise ValueError('Symlink paths are not supported')
        resolved = p.resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ValueError('Path escapes repository')
        if not p.exists() or (file and not p.is_file()):
            raise ValueError('Source path does not exist or is not a file')
        return p

    def read(self, request):
        if self.control()['phase'] == 'submit':
            raise ValueError('Submission phase: source searches are closed; save candidates using known locations')
        p = self.source(request.get('path', '.'))
        op = request.get('operation', 'read')
        rows = []
        if op == 'list':
            if not p.is_dir():
                raise ValueError('list requires a directory')
            rows = sorted(str(q.relative_to(self.root)) + ('/' if q.is_dir() else '')
                          for q in p.iterdir() if q.name not in EXCLUDED and not q.is_symlink())[:100]
        elif op == 'read':
            p = self.source(request.get('path', ''), file=True)
            if p.stat().st_size > 2_000_000:
                raise ValueError('File too large; choose a focused source file')
            lines = p.read_text().splitlines()
            start = request.get('start_line', 1)
            if type(start) is not int:
                raise ValueError('start_line must be an integer, not a quoted string')
            end = request.get('end_line', min(start+149, len(lines)))
            if type(start) is not int or type(end) is not int or start < 1 or end < start or start > len(lines):
                raise ValueError('Invalid source line range')
            end = min(end, start+199, len(lines))
            rows = [f'{p.relative_to(self.root)}:{i+1}: {lines[i]}' for i in range(start-1, end)]
        elif op == 'search':
            pattern = request.get('pattern', '')
            if not isinstance(pattern, str) or not pattern or len(pattern) > 500:
                raise ValueError('search needs a short non-empty pattern')
            regex = re.compile(pattern, re.IGNORECASE if request.get('ignore_case') else 0)
            def files():
                if p.is_file():
                    yield p
                    return
                for base, dirs, names in os.walk(p, followlinks=False):
                    dirs[:] = sorted(d for d in dirs if d not in EXCLUDED and not (Path(base)/d).is_symlink())
                    for name in sorted(names):
                        q = Path(base)/name
                        if not q.is_symlink() and name not in EXCLUDED:
                            yield q
            scanned = 0
            for q in files():
                if scanned >= 5000 or len(rows) >= 100:
                    break
                scanned += 1
                if q.stat().st_size > 1_000_000:
                    continue
                try:
                    source = q.read_text()
                except (OSError, UnicodeError):
                    continue
                if '\0' in source:
                    continue
                for i, line in enumerate(source.splitlines(), 1):
                    if regex.search(line):
                        rows.append(f'{q.relative_to(self.root)}:{i}: {line[:1000]}')
                        if len(rows) >= 100:
                            break
        else:
            raise ValueError('operation must be list, read, or search')
        text = '\n'.join(rows)[:20000]
        return text or 'No matches. This is not new positive evidence.', {
            'ok': True, 'evidence_digest': hashlib.sha256(text.encode()).hexdigest() if text else None}

    def checkpoint(self, record):
        record = copy.deepcopy(record)
        operation = record.pop('operation', 'save')
        if operation not in ('save', 'update'):
            raise ValueError('checkpoint operation must be save or update')
        if operation == 'update':
            previous = self.load().get('investigation', {})
            if not previous:
                raise ValueError('Save the first full checkpoint before incremental updates')
            merged = copy.deepcopy(previous)
            for field in ('hypotheses', 'location_decisions'):
                if field in record:
                    rows = {r['id']: r for r in merged.get(field, [])}
                    for row in record.pop(field):
                        old = rows.get(row['id'], {})
                        if field == 'location_decisions' and 'options' in row:
                            opts = {o['id']: o for o in old.get('options', [])}
                            for o in row['options']:
                                opts[o['id']] = {**opts.get(o['id'], {}), **o}
                            row['options'] = list(opts.values())
                        rows[row['id']] = {**old, **row}
                    merged[field] = list(rows.values())
            for field in ('confirmed_facts', 'open_questions'):
                for value in record.pop(field + '_add', []):
                    if value not in merged[field]:
                        merged[field].append(value)
            merged.update(record)
            record = merged
        for key in ('requirements', 'confirmed_facts', 'open_questions'):
            if not isinstance(record.get(key), list) or not all(isinstance(v, str) for v in record[key]):
                raise ValueError(f'{key} must be a list of strings')
        if not record['requirements']:
            raise ValueError('Record the original issue requirements')
        hypotheses = record.get('hypotheses')
        if not isinstance(hypotheses, list):
            raise ValueError('hypotheses must be a list; do not invent hypotheses if evidence is insufficient')
        ids = set()
        for h in hypotheses:
            for key in ('id', 'hypothesis', 'rationale'):
                if not isinstance(h.get(key), str) or not h[key].strip():
                    raise ValueError(f'Each hypothesis needs {key}')
            if h['id'] in ids:
                raise ValueError(f'Duplicate hypothesis id {h["id"]!r}; keep each ID exactly once')
            if h.get('status') not in STATUSES:
                raise ValueError(f'Hypothesis {h["id"]!r} has invalid status {h.get("status")!r}. '
                                 f'Allowed statuses: {", ".join(sorted(STATUSES))}. '
                                 '"merged" is NOT a status: keep the hypothesis plausible/supported/unresolved, '
                                 'and link it through hypothesis_ids on the candidate with the identical full '
                                 'location set. First fix this checkpoint status, then update that candidate.')
            if not isinstance(h.get('questions'), list) or not all(isinstance(q, str) for q in h['questions']):
                raise ValueError('Each hypothesis needs a questions list')
            validate_rejection(h)
            h['source_version'] = hashlib.sha256(json.dumps(
                {key: h[key] for key in ('hypothesis', 'rationale', 'questions')},
                sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            ids.add(h['id'])
        data = self.load()
        decisions = decision_options(record)
        previous = data.get('investigation', {})
        previous_decisions = decision_options(previous) if 'location_decisions' in previous else {}
        if set(previous_decisions) - set(decisions) or any(
                set(options) - set(decisions[did]) for did, options in previous_decisions.items() if did in decisions):
            raise ValueError('Keep all recorded location decisions/options; reject impossible options with evidence')
        old_ids = {h['id'] for h in data.get('investigation', {}).get('hypotheses', [])}
        if old_ids - ids:
            raise ValueError('Keep all previously recorded hypotheses; do not silently drop alternatives')
        if self.control()['phase'] in ('consolidate', 'submit') and ids - old_ids:
            raise ValueError('New hypotheses are closed; finish existing plans')
        if self.control()['phase'] in ('consolidate', 'submit') and set(decisions) - set(previous_decisions):
            raise ValueError('New location decisions are closed; finish the recorded choices')
        action = record.get('next_action', 'continue')
        if action not in ('continue', 'switch', 'finalize'):
            raise ValueError('next_action must be continue, switch, or finalize')
        active = record.get('active_hypothesis')
        if active is not None and active not in ids:
            raise ValueError('active_hypothesis must reference a recorded hypothesis')
        if active is not None and next(h for h in hypotheses if h['id'] == active)['status'] == 'rejected':
            raise ValueError('Do not continue exploring a rejected hypothesis')
        if self.policy == DYNAMIC_POLICY:
            previous_by_id = {h['id']: h for h in previous.get('hypotheses', [])}
            history = data.setdefault('hypothesis_history', [])
            for h in hypotheses:
                old = previous_by_id.get(h['id'])
                if old is None or old.get('source_version') != h['source_version']:
                    history.append({k: copy.deepcopy(h[k]) for k in
                                    ('id', 'hypothesis', 'rationale', 'questions', 'source_version')})
        data['investigation'] = record
        data['candidate_policy'] = self.policy
        self.write(data)
        return ('Investigation checkpoint saved. Requirements are numbered 1..%d. '
                'If hypothesis text/questions or requirements changed, reconcile and re-save affected candidates.'
                % len(record['requirements'])), {'ok': True, 'active_hypothesis': active,
                'next_action': action, 'candidate_count': len(data['candidates']),
                'stale_candidates': [c['id'] for c in data['candidates'] if c.get('hypothesis_versions') !=
                    {h['id']: h['source_version'] for h in hypotheses if h['id'] in hypothesis_ids(c)}]}

    def candidate(self, request):
        data = self.load()
        operation = request.get('operation', 'save')
        if data.get('candidate_policy') in (STRICT_POLICY, DYNAMIC_POLICY) and operation != 'remove':
            return self.compiled_candidate(data, request)
        before = json.dumps(data['candidates'], sort_keys=True)
        if operation == 'save':
            c = copy.deepcopy(request.get('candidate', {}))
            for key in ('id', 'hypothesis', 'hypothesis_id'):
                if not isinstance(c.get(key), str) or not c[key].strip():
                    raise ValueError(f'candidate needs {key}')
            hypotheses = {h['id']: h for h in data.get('investigation', {}).get('hypotheses', [])}
            if any(ref not in hypotheses or hypotheses[ref]['status'] == 'rejected' for ref in hypothesis_ids(c)):
                raise ValueError('Record this non-rejected hypothesis with location_checkpoint first')
            if not isinstance(c.get('evidence'), list) or not c['evidence'] or not all(map(nonempty, c['evidence'])):
                raise ValueError('candidate needs self-contained supporting evidence')
            record = data['investigation']
            c['schema_version'] = CANDIDATE_SCHEMA
            c['requirements'] = copy.deepcopy(record['requirements'])
            c['hypothesis_versions'] = {ref: hypotheses[ref]['source_version'] for ref in hypothesis_ids(c)}
            c['selected_location_choices'] = selected_choices(record, c.get('location_choices'))
            validate_candidate_contract(c)
            files = c.get('files_to_modify')
            if not isinstance(files, list) or not files:
                raise ValueError('candidate needs the complete files_to_modify list')
            seen = set()
            for f in files:
                p = self.source(f.get('file_path', ''), file=True)
                if p.stat().st_size > 2_000_000:
                    raise ValueError('Source file too large')
                lines = p.read_text().splitlines()
                mods = f.get('modifications')
                if not isinstance(mods, list) or not mods:
                    raise ValueError('Each file needs modifications with location start_line/end_line')
                for m in mods:
                    loc = m.get('location', {})
                    start, end = loc.get('start_line'), loc.get('end_line')
                    if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
                        raise ValueError(f'Invalid range for {f["file_path"]}; file has {len(lines)} lines')
                    key = (str(p.relative_to(self.root)), start, end)
                    if key in seen:
                        raise ValueError('Duplicate range in candidate')
                    seen.add(key)
                    # Preserve selected trailing blank lines under splitlines(),
                    # which the submission validator uses for exact comparison.
                    m['current_code'] = '\n'.join(lines[start-1:end]) + '\n'
                    if not m['current_code'].strip():
                        raise ValueError('Use a non-empty source anchor')
            def signature(item):
                return sorted((f['file_path'], m['location']['start_line'], m['location']['end_line'])
                              for f in item['files_to_modify'] for m in f['modifications'])
            for other in data['candidates']:
                if (other['id'] != c['id'] and signature(other) == signature(c)
                        and other.get('hypothesis') == c['hypothesis']
                        and other.get('location_choices') == c['location_choices']):
                    raise ValueError(f'Same complete set and selected strategy as {other["id"]}; '
                                     'update that candidate with merged evidence/questions')
            data['candidates'] = [x for x in data['candidates'] if x['id'] != c['id']] + [c]
        elif operation == 'remove':
            if not request.get('reason'):
                raise ValueError('Record why the candidate is being removed')
            existing = next((c for c in data['candidates'] if c['id'] == request.get('id')), None)
            if existing is None:
                raise ValueError('Candidate to remove does not exist')
            hypotheses = {h['id']: h for h in data.get('investigation', {}).get('hypotheses', [])}
            for ref in hypothesis_ids(existing):
                h = hypotheses.get(ref, {})
                if h.get('status') != 'rejected':
                    raise ValueError('Cannot remove an unrefuted strategy; record concrete rejection_evidence '
                                     'with location_checkpoint first')
                validate_rejection(h)
            data['candidates'] = [x for x in data['candidates'] if x['id'] != request.get('id')]
            data.setdefault('candidate_rejections', []).append({
                'id': request.get('id'), 'reason': request['reason'], 'candidate': existing,
                'rejection_evidence': {ref: hypotheses[ref]['rejection_evidence'] for ref in hypothesis_ids(existing)}})
        elif operation != 'list':
            raise ValueError('operation must be save, remove, or list')
        changed = before != json.dumps(data['candidates'], sort_keys=True)
        self.write(data)
        summary = [{'id': c['id'], 'hypothesis': c['hypothesis'], 'files': [f['file_path'] for f in c['files_to_modify']]}
                   for c in data['candidates']]
        return json.dumps(summary, ensure_ascii=False), {'ok': True, 'candidate_count': len(summary), 'changed': changed}

    def compiled_candidate(self, data, request):
        operation = request.get('operation', 'save')
        by_id = {c['id']: c for c in data['candidates']}
        if operation == 'review':
            return self.prepare_review(data, request.get('id'))
        if operation == 'get':
            c = by_id.get(request.get('id'))
            if c is None:
                raise ValueError('Unknown candidate ID')
            compact = {k: c[k] for k in ('id', 'hypothesis_id', 'hypothesis', 'plan')}
            versions = {h['id']: h['source_version'] for h in data['investigation']['hypotheses'] if h['id'] in hypothesis_ids(c)}
            compact['needs_sync'] = c['hypothesis_versions'] != versions
            compact['review_status'] = ('stale' if compact['needs_sync'] else
                                        c.get('semantic_review', {}).get('status', 'not_reviewed'))
            compact['review_findings'] = [a for a in c.get('semantic_review', {}).get('assessments', [])
                                          if a['verdict'] != 'consistent']
            if c.get('context'):
                compact['context'] = c['context']
            return json.dumps(compact, ensure_ascii=False), {'ok': True, 'candidate_count': len(by_id)}
        if operation in ('list', 'validate'):
            try:
                validate_retention(data)
                readiness = 'ready'
            except ValueError as exc:
                readiness = str(exc)
            return json.dumps({'candidates': list(by_id), 'submission_check': readiness}), {'ok': True, 'candidate_count': len(by_id)}
        if operation == 'save':
            c = copy.deepcopy(request.get('candidate'))
            compiler()['keys'](c, ('id', 'hypothesis_id', 'hypothesis_ids', 'evidence', 'plan', 'context'), 'candidate input')
        elif operation in ('revise', 'fork'):
            compiler()['keys'](request, ('operation', 'id', 'from', 'hypothesis_id', 'hypothesis_ids', 'evidence',
                'upsert_changes', 'remove_changes', 'upsert_checks', 'remove_checks', 'choices', 'unchanged_requirements', 'context'), operation)
            source = by_id.get(request.get('from') if operation == 'fork' else request.get('id'))
            if source is None:
                raise ValueError('Unknown source candidate')
            if operation == 'fork' and request.get('id') in by_id:
                raise ValueError('fork needs a new candidate ID; the original is preserved')
            c = {k: copy.deepcopy(source[k]) for k in ('id', 'hypothesis_id', 'hypothesis_ids', 'evidence', 'context') if k in source}
            for k in ('id', 'hypothesis_id', 'hypothesis_ids', 'evidence', 'context'):
                if k in request:
                    c[k] = copy.deepcopy(request[k])
            if 'hypothesis_id' in request and 'hypothesis_ids' not in request:
                c.pop('hypothesis_ids', None)
            c['plan'] = compiler()['revise_plan'](source['plan'], request)
        else:
            raise ValueError('Use save/revise/fork/get/list/validate/review/remove')
        if not nonempty(c.get('id')):
            raise ValueError('Candidate needs a non-empty id')
        record = data['investigation']
        hypotheses = {h['id']: h for h in record['hypotheses']}
        refs = hypothesis_ids(c)
        if any(ref not in hypotheses or hypotheses[ref]['status'] == 'rejected' for ref in refs):
            raise ValueError('Record all referenced non-rejected hypotheses first')
        c['requirements'] = copy.deepcopy(record['requirements'])
        c['hypothesis_versions'] = {ref: hypotheses[ref]['source_version'] for ref in refs}
        c.setdefault('evidence', [hypotheses[ref]['rationale'] for ref in sorted(refs)])
        def read_source(path, start, end):
            p = self.source(path, file=True)
            if p.stat().st_size > 2_000_000:
                raise ValueError('Source file too large')
            lines = p.read_text().splitlines()
            if end > len(lines):
                raise ValueError(f'Invalid range for {path}: file has {len(lines)} lines')
            return '\n'.join(lines[start-1:end]) + '\n'
        dynamic = data['candidate_policy'] == DYNAMIC_POLICY
        c.update(compiler()['compile_plan'](c.get('plan'), c['requirements'], read_source, dynamic=dynamic))
        if dynamic:
            c['hypothesis_snapshots'] = {hid: {k: copy.deepcopy(hypotheses[hid][k])
                for k in ('hypothesis', 'rationale', 'questions')} for hid in sorted(refs)}
            c['hypothesis'] = hypotheses[c['hypothesis_id']]['hypothesis']

        c.setdefault('context', [])
        if not isinstance(c['context'], list):
            raise ValueError('context must be a list of additional read-only source ranges')
        c['review_context'] = []
        for ref in c['context']:
            compiler()['keys'](ref, ('file_path', 'start_line', 'end_line'), 'read-only context')
            start, end = ref.get('start_line'), ref.get('end_line')
            if type(start) is not int or type(end) is not int or not 1 <= start <= end or end-start >= 200:
                raise ValueError('Each read-only context excerpt needs a valid range of at most 200 lines')
            c['review_context'].append({**ref, 'current_code': read_source(ref.get('file_path'), start, end)})
        c['schema_version'] = DYNAMIC_SCHEMA if dynamic else STRICT_SCHEMA
        c['selected_location_choices'] = selected_choices(record, c['location_choices'])
        validate_candidate_contract(c)
        old = by_id.get(c['id'])
        if old and candidate_fingerprint(old) == candidate_fingerprint(c) and 'semantic_review' in old:
            c['semantic_review'] = copy.deepcopy(old['semantic_review'])
        data['candidates'] = [item for item in data['candidates'] if item['id'] != c['id']] + [c]
        self.write(data)
        summary = {'id': c['id'], 'changes': [v['id'] for v in c['plan']['changes']],
                   'locations': len([m for f in c['files_to_modify'] for m in f['modifications']]),
                   'checks': len(c['unresolved_questions']), 'review': c.get('semantic_review', {}).get('status', 'required')}
        return json.dumps(summary), {'ok': True, 'candidate_count': len(data['candidates']), 'changed': old != c}

    def prepare_review(self, data, identifier=None):
        validate_retention(data, require_review=False)
        c = next((c for c in data['candidates'] if c['id'] == identifier), None) if identifier else None
        if identifier and c is None:
            raise ValueError('Unknown candidate ID for semantic review')
        if c is None:
            for item in data['candidates']:
                try:
                    validate_semantic_review(item)
                except ValueError:
                    c = item;break
        if c is None:
            return 'All candidates have current semantic reviews; submit.', {'ok': True, 'review_pending': False}
        cached = c.get('semantic_review', {})
        if cached.get('fingerprint') == candidate_fingerprint(c) and cached.get('status') == 'needs_revision':
            return ('[语义审查待修正] Cached findings: revise the plan or add source context/evidence before another review. '
                    + json.dumps([a for a in cached['assessments'] if a['verdict'] != 'consistent'], ensure_ascii=False)), {
                        'ok': True, 'review_pending': False}
        try:
            validate_semantic_review(c)
            return f'{c["id"]}: unchanged reviewed plan; cached review reused.', {'ok': True, 'review_pending': False}
        except ValueError:
            pass
        excerpts = []
        for f in c['files_to_modify']:
            lines = self.source(f['file_path'], file=True).read_text().splitlines()
            selected = set()
            for m in f['modifications']:
                selected.update(range(max(1, m['location']['start_line']-6), min(len(lines), m['location']['end_line']+6)+1))
            excerpts.append({'file_path': f['file_path'], 'source': '\n'.join(f'{i}: {lines[i-1]}' for i in sorted(selected))})
        # The next ordinary agent round uses this packet instead of the long exploration history.
        packet = {'candidate_id': c['id'], 'fingerprint': candidate_fingerprint(c),
                  'requirements': c['requirements'], 'hypothesis': c['hypothesis'], 'plan': c['plan'],
                  'selected_location_choices': c['selected_location_choices'],
                  'unresolved_questions': c['unresolved_questions'], 'evidence': c['evidence'], 'source': excerpts,
                  'read_only_context_not_edit_locations': c.get('review_context', []),
                  'targets': [{'type': kind, 'id': identifier} for kind, identifier in sorted(review_targets(c))]}
        if c.get('schema_version') == DYNAMIC_SCHEMA:
            packet['hypothesis_snapshots'] = c['hypothesis_snapshots']
            packet['repair_plan'] = c['repair_plan']
        if len(json.dumps(packet, ensure_ascii=False)) > 100_000:
            raise ValueError('Semantic review packet exceeds 100k characters; narrow source ranges before review (no silent truncation)')
        self.artifacts.mkdir(parents=True, exist_ok=True)
        path = self.artifacts/'location_review_request.json'
        tmp = path.with_suffix('.tmp');tmp.write_text(json.dumps(packet, ensure_ascii=False));tmp.replace(path)
        return f'Semantic review queued for {c["id"]}; the next counted round checks meaning against source.', {'ok': True, 'review_pending': True}

    def review(self, request):
        pending = self.artifacts/'location_review_request.json'
        if not pending.exists():
            raise ValueError('No semantic review is pending')
        packet = json.loads(pending.read_text())
        data = self.load()
        c = next((c for c in data['candidates'] if c['id'] == packet['candidate_id']), None)
        if (c is None or request.get('candidate_id') != c['id'] or request.get('fingerprint') != packet['fingerprint']
                or candidate_fingerprint(c) != packet['fingerprint']):
            raise ValueError('Semantic review does not match the current candidate fingerprint')
        assessments = request.get('assessments')
        validate_assessments(c, assessments)
        failures = [a for a in assessments if a['verdict'] != 'consistent']
        c['semantic_review'] = {'fingerprint': packet['fingerprint'], 'status': 'needs_revision' if failures else 'passed',
                                'assessments': assessments, 'method': 'isolated_counted_model_round'}
        self.write(data)
        pending.unlink()
        result = json.dumps({'candidate_id': c['id'], 'status': c['semantic_review']['status'], 'findings': failures},
                            ensure_ascii=False)
        if failures:
            result = '[语义审查待修正] ' + result
        return result, {'ok': True, 'review_pending': False, 'candidate_count': len(data['candidates'])}

    def abort(self, reason):
        if self.load()['candidates']:
            raise ValueError('Candidates remain: submit supported plans or explicitly remove refuted candidates first')
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('Explain which evidence or coverage is missing')
        self.artifacts.mkdir(parents=True, exist_ok=True)
        (self.artifacts/'localization_no_candidate.json').write_text(json.dumps({'reason': reason}, ensure_ascii=False))
        return 'Localization ended without a supported complete candidate: ' + reason, {'ok': True, 'aborted': True}


def main(name):
    from registry import registry
    try:
        if len(sys.argv) != 2:
            raise ValueError('Supply one JSON request argument (location_abort takes a plain reason)')
        root = registry.get('ROOT')
        if not root:
            raise ValueError('Repository ROOT unavailable; environment initialization failed')
        store = Store(root)
        method = getattr(store, name.removeprefix('location_'))
        payload, normalized = (sys.argv[1], False) if name == 'location_abort' else decode_request(sys.argv[1])
        text, event = method(payload)
        if normalized:
            print('Request normalized: removed redundant request wrapper or preserved a regex escape.')
        print(text)
        print('LOCATION_STATE ' + json.dumps(event))
    except (ValueError, KeyError, TypeError, AttributeError, OSError, UnicodeError, re.error) as exc:
        if name == 'location_review' and 'store' in locals():
            pending = store.artifacts/'location_review_request.json'
            if pending.exists():
                packet = json.loads(pending.read_text());packet['last_error'] = str(exc)
                pending.write_text(json.dumps(packet, ensure_ascii=False))
        print('Localization tool error: ' + str(exc))
        print('LOCATION_STATE ' + json.dumps({'ok': False}))
        sys.exit(1)
