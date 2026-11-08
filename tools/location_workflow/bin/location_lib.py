"""Simple complete location sets with checks; source access never executes repository code."""
import copy
import hashlib
import json
import os
import re
import runpy
import sys
from functools import lru_cache
from pathlib import Path

EXCLUDED = {'.git', '.hg', '.svn', '__pycache__', '.venv', 'node_modules', '.pytest_cache'}
SIMPLE_POLICY = 'locations_and_checks_v1'
SIMPLE_SCHEMA = 'location_checks_v1'
EXPLORATION_POLICY = 'independent_hypotheses_v1'


@lru_cache(maxsize=1)
def hypothesis_queue():
    return runpy.run_path(str(Path(__file__).with_name('hypothesis_queue.py')))


@lru_cache(maxsize=1)
def complete_strategy():
    return runpy.run_path(str(Path(__file__).with_name('complete_strategy.py')))


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


@lru_cache(maxsize=1)
def legacy_contract():
    # Read old results without restoring their workflow in new experiments.
    return runpy.run_path(str(Path(__file__).with_name('legacy_location_lib.py')))


def validate_candidate_contract(candidate, *, allow_duplicate_ranges=False):
    if candidate.get('schema_version') != SIMPLE_SCHEMA:
        return legacy_contract()['validate_candidate_contract'](candidate)
    if not nonempty(candidate.get('id')):
        raise ValueError('Candidate needs an id')
    files = candidate.get('files_to_modify')
    if not isinstance(files, list) or not files:
        raise ValueError('Supply the complete files_to_modify list')
    ids, anchors, paths = set(), set(), set()
    for f in files:
        path = f.get('file_path')
        if not nonempty(path) or Path(path).is_absolute() or any(x in EXCLUDED or x == '..' for x in Path(path).parts):
            raise ValueError('Use a relative current-source file_path')
        if str(Path(path)) in paths:
            raise ValueError('List each file once with all its cooperating modifications')
        paths.add(str(Path(path)))
        operation = f.get('operation', 'modify')
        if operation not in ('modify', 'create'):
            raise ValueError('File operation must be modify or create')
        mods = f.get('modifications')
        if not isinstance(mods, list) or not mods:
            raise ValueError('Each file needs modifications')
        for m in mods:
            lid = m.get('id');loc = m.get('location') or {}
            start, end = loc.get('start_line'), loc.get('end_line')
            if not nonempty(lid) or lid in ids:
                raise ValueError('Each location needs a unique id such as L1')
            ids.add(lid)
            if operation == 'create':
                if m.get('location') is not None or m.get('current_code') != '':
                    raise ValueError('New files have no original line range and current_code must be empty')
                if not nonempty(m.get('reason')):
                    raise ValueError('Describe what the new file must provide in reason')
            else:
                if type(start) is not int or type(end) is not int or not 1 <= start <= end:
                    raise ValueError('Location needs valid integer start_line/end_line')
                anchor = (path, start, end)
                if anchor in anchors and not allow_duplicate_ranges:
                    raise ValueError('Do not repeat the same source range')
                anchors.add(anchor)
                if not nonempty(m.get('current_code')):
                    raise ValueError('Location needs a non-empty source anchor')
            if 'reason' in m and not nonempty(m['reason']):
                raise ValueError('Optional reason must be a short non-empty intended change')
    if candidate.get('planning_mode') == 'complete_strategy_v1':
        complete_strategy()['validate_plan'](candidate)
        return
    if candidate.get('planning_mode') == 'locations_only_v1':
        # New exploration emits locations and short intentions, not test plans.
        for file in files:
            if any(not nonempty(m.get('reason')) for m in file['modifications']):
                raise ValueError('Each planned location needs a short intended change in reason')
        return
    questions = candidate.get('unresolved_questions')
    if not isinstance(questions, list) or not questions:
        raise ValueError('Supply candidate.unresolved_questions alongside candidate.files_to_modify: '
                         '[{"question":"Required behavior to verify", "location_ids":["L1"]}]. '
                         'Every location needs a check; do not omit questions when correcting file ranges.')
    covered = set()
    for q in questions:
        if not isinstance(q, dict) or not nonempty(q.get('question')):
            raise ValueError('Each verification point needs question and location_ids')
        refs = q.get('location_ids')
        if (not isinstance(refs, list) or not refs or not all(nonempty(x) for x in refs)
                or len(set(refs)) != len(refs) or set(refs) - ids):
            raise ValueError('Verification location_ids must refer to locations in THIS candidate: ' + ', '.join(sorted(ids)))
        covered.update(refs)
    if ids - covered:
        raise ValueError('Add verification points covering these locations: ' + ', '.join(sorted(ids-covered)))


def validate_retention(data, **kwargs):
    if data.get('candidate_policy') != SIMPLE_POLICY:
        kwargs.pop('control', None)
        return legacy_contract()['validate_retention'](data, **kwargs)
    candidates = data.get('candidates')
    if not isinstance(candidates, list) or not candidates:
        raise ValueError('Save at least one complete candidate')
    ids = set()
    for c in candidates:
        if c.get('schema_version') != SIMPLE_SCHEMA:
            raise ValueError('Save locations and checks with location_candidate')
        validate_candidate_contract(c)
        if c['id'] in ids:
            raise ValueError('Candidate IDs must be unique')
        ids.add(c['id'])
    if data.get('exploration_policy') == EXPLORATION_POLICY:
        hypothesis_queue()['finalize'](data, kwargs.get('control'))
    if data.get('plan_contract') == 'complete_strategy_v1':
        complete_strategy()['validate_all'](data)


def finalize_analysis(data, control=None):
    if data.get('candidate_policy') != SIMPLE_POLICY:
        return legacy_contract()['finalize_analysis'](data)
    validate_retention(data, control=control)
    result = {'candidate_policy': SIMPLE_POLICY, 'candidates': copy.deepcopy(data['candidates']), 'finalized': True}
    if data.get('exploration_policy') == EXPLORATION_POLICY:
        result.update(hypothesis_queue()['finalize'](data, control))
    if data.get('plan_contract') == 'complete_strategy_v1':
        result['plan_contract'] = data['plan_contract']
    return result


def validate_semantic_review(candidate):
    # Compatibility only: new candidates never request an isolated model review.
    return legacy_contract()['validate_semantic_review'](candidate)


def normalize_question_placement(candidate):
    """Lift misplaced checks without guessing references or rewriting their text."""
    notes = []
    questions = candidate.setdefault('unresolved_questions', [])
    if not isinstance(questions, list):
        raise ValueError('candidate.unresolved_questions must be an array of question/location_ids objects')
    for f in candidate.get('files_to_modify', []):
        containers = [(f, f'file {f.get("file_path", "?")}')]
        containers += [(m, f'location {m.get("id", "?")}') for m in f.get('modifications', [])]
        for obj, label in containers:
            if 'unresolved_questions' not in obj:
                continue
            misplaced = obj.pop('unresolved_questions')
            if not isinstance(misplaced, list):
                raise ValueError(f'{label}.unresolved_questions must be an array with explicit location_ids; '
                                 'put it alongside candidate.files_to_modify')
            for q in misplaced:
                if (not isinstance(q, dict) or not nonempty(q.get('question'))
                        or not isinstance(q.get('location_ids'), list) or not q['location_ids']):
                    raise ValueError(f'{label}: misplaced verification point needs question and explicit location_ids; '
                                     'cannot infer which edits it verifies')
                if q not in questions:
                    questions.append(q)
            notes.append(f'Moved {len(misplaced)} verification point(s) from {label} to candidate.unresolved_questions; '
                         'text and explicit references preserved.')
    return notes


def reference_notices(candidate):
    """Advisory only: compare explicit prose line references to the selected ranges.

    No repository searches, inferred edits or semantic rejection. Ambiguous file
    names and unqualified references across multiple files are left to the agent.
    """
    ranges, id_paths = {}, {}
    for f in candidate['files_to_modify']:
        path = f['file_path']
        ranges[path] = [m['location'] for m in f['modifications'] if m.get('location')]
        id_paths.update({m['id']: path for m in f['modifications']})
    texts = [(m['reason'], f['file_path'], m['id']) for f in candidate['files_to_modify']
             for m in f['modifications'] if m.get('reason')]
    for i, q in enumerate(candidate['unresolved_questions'], 1):
        paths = {id_paths[x] for x in q['location_ids']}
        texts.append((q['question'], next(iter(paths)) if len(paths) == 1 else None, f'check {i}'))
    qualified = re.compile(
        r'(?P<path>(?:[\w.-]+/)*[\w.-]+\.(?:py|pyi|yaml|yml|json|js|ts|tsx|jsx|sh|md|asciidoc))'
        r'`?(?::L?|\s+(?:at\s+)?lines?\s+)(?P<start>\d+)(?:\s*[-–—]\s*(?:L)?(?P<end>\d+))?', re.I)
    bare = re.compile(r'\blines?\s+(?P<start>\d+)(?:\s*[-–—]\s*(?P<end>\d+))?', re.I)
    notices, seen = [], set()
    for text, default, origin in texts:
        matches = list(qualified.finditer(text))
        refs = [(m.group('path'), m) for m in matches]
        refs += [(default, m) for m in bare.finditer(text)
                 if default and not any(q.start() <= m.start() < q.end() for q in matches)]
        for path, match in refs:
            if path not in ranges:
                choices = [p for p in ranges if p.endswith('/' + path)]
                if default in choices:
                    path = default
                elif len(choices) == 1:
                    path = choices[0]
                elif len(choices) > 1:
                    continue
            start = int(match.group('start')); end = int(match.group('end') or start)
            if start < 1 or end < start:
                continue
            cursor = start
            for loc in sorted(ranges.get(path, []), key=lambda x: x['start_line']):
                if loc['start_line'] > cursor:
                    break
                cursor = max(cursor, loc['end_line'] + 1)
            key = (path, start, end)
            if cursor <= end and key not in seen:
                seen.add(key)
                notices.append({'origin': origin, 'file_path': path, 'start_line': start, 'end_line': end,
                                'message': f'{origin} mentions {path}:{start}-{end}, but the candidate does not '
                                           'cover that full range. If it must change, add its location; if the '
                                           'description is wrong, correct it. Unchanged context need not be added.'})
    return notices[:8]


def merge_overlapping_locations(candidate):
    """Merge only overlapping edits within ONE candidate; preserve reasons/checks."""
    aliases = {}
    for f in candidate['files_to_modify']:
        if f.get('operation') == 'create':
            continue
        merged = []
        for m in sorted(f['modifications'], key=lambda x: x['location']['start_line']):
            if not merged or m['location']['start_line'] > merged[-1]['location']['end_line']:
                merged.append(m)
                continue
            previous = merged[-1]
            previous['location']['end_line'] = max(previous['location']['end_line'], m['location']['end_line'])
            aliases[m['id']] = previous['id']
            reasons = list(dict.fromkeys(x for x in (previous.get('reason'), m.get('reason')) if x))
            if reasons:
                previous['reason'] = '; '.join(reasons)
        f['modifications'] = merged
    for q in candidate['unresolved_questions']:
        q['location_ids'] = list(dict.fromkeys(aliases.get(x, x) for x in q['location_ids']))
    if candidate.get('planning_mode') == 'complete_strategy_v1':
        complete_strategy()['remap'](candidate, aliases)
    return aliases


def repair_request_json(text):
    """Repair only literal string escapes and missing closing delimiters.

    Never add keys/values, commas or quotes, remove input, or move fields. The
    caller must parse the ENTIRE result and validate the usual tool contract.
    """
    output, stack, repairs = [], [], []
    quoted = False
    index = 0
    closing_count = 0
    pairs = {'{': '}', '[': ']'}

    def close_missing():
        nonlocal closing_count
        if text[:index].rstrip().endswith(('{', '[', ':', ',')):
            raise ValueError('Unfinished container or value; resend the complete request')
        closing_count += 1
        if closing_count > 6:
            raise ValueError('More than 6 missing closing delimiters; resend a complete JSON request')
        closer = pairs[stack.pop()]
        output.append(closer)
        repairs.append(f'Inserted missing {closer} at input offset {index}')

    while index < len(text):
        char = text[index]
        if quoted:
            if char == '\\':
                if index + 1 >= len(text):
                    raise ValueError('Unfinished string escape; resend the complete string')
                following = text[index + 1]
                if following in '"\\/bfnrtu':
                    output.extend((char, following))
                    index += 2
                    continue
                # Preserve literal regex escapes (e.g. \s); do not touch valid
                # escaped backslashes/quotes or guess a malformed Unicode escape.
                output.append('\\\\')
                repairs.append(f'Preserved literal backslash at input offset {index}')
            elif char == '"':
                quoted = False
                output.append(char)
            elif ord(char) < 32:
                output.append(json.dumps(char)[1:-1])
                repairs.append(f'Escaped literal control character at input offset {index}')
            else:
                output.append(char)
        elif char == '"':
            quoted = True
            output.append(char)
        elif char in pairs:
            stack.append(char)
            output.append(char)
        elif char in '}]':
            opener = '{' if char == '}' else '['
            if opener not in stack:
                raise ValueError('Unmatched closing delimiter; resend balanced JSON')
            while stack[-1] != opener:
                close_missing()
            stack.pop()
            output.append(char)
        else:
            output.append(char)
        index += 1
    if quoted:
        raise ValueError('Unterminated string; resend the complete string')
    while stack:
        close_missing()
    return ''.join(output), repairs


def decode_request(raw, *, diagnostics=None):
    """Decode JSON and bounded formatting repairs, never evaluate generated code."""
    notes = []

    def parse(text):
        try:
            return json.loads(text)
        except json.JSONDecodeError as original:
            try:
                repaired, changes = repair_request_json(text)
                result = json.loads(repaired)
            except (ValueError, RecursionError) as failure:
                raise ValueError(
                    f'Invalid request JSON at line {original.lineno}, column {original.colno}: '
                    f'{original.msg}. Limited format repair could not resolve it: {failure}. '
                    'Resend the full request with quoted keys/strings and balanced brackets; '
                    'include all locations and verification points, not just the corrected fragment.'
                ) from original
            notes.extend(changes)
            return result
    # Native function calls pass an object; CLI wrappers still pass JSON text.
    value = copy.deepcopy(raw) if isinstance(raw, dict) else parse(raw)
    for _ in range(3):
        if isinstance(value, dict) and 'request' in value and set(value) <= {'request', 'operation'}:
            operation = value.get('operation')
            value = value['request']
            if isinstance(value, str):
                value = parse(value)
            if operation is not None and isinstance(value, dict):
                if 'operation' in value and value['operation'] != operation:
                    raise ValueError('Conflicting outer and inner operation values')
                value = dict(value, operation=operation)
            notes.append('Removed redundant request wrapper')
        else:
            break
    if not isinstance(value, dict):
        raise ValueError('Request must be a JSON object')
    if diagnostics is not None:
        diagnostics.extend(notes)
    return value, bool(notes)


class Store:
    def __init__(self, root, artifacts='/root'):
        self.root = Path(root).resolve()
        self.artifacts = Path(artifacts)
        self.path = self.artifacts / 'analysis.json'

    def load(self):
        data = json.loads(self.path.read_text()) if self.path.exists() else {'investigation': {}, 'candidates': []}
        if self.control().get('exploration_policy') == EXPLORATION_POLICY:
            data['exploration_policy'] = EXPLORATION_POLICY
        if self.control().get('plan_contract') == 'complete_strategy_v1':
            data['plan_contract'] = 'complete_strategy_v1'
        return data

    def write(self, data):
        self.artifacts.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        tmp.replace(self.path)

    def control(self):
        p = self.artifacts / 'location_control.json'
        return json.loads(p.read_text()) if p.exists() else {'phase': 'map'}

    def source(self, name, file=False, create=False):
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
        if create:
            if p.exists():
                raise ValueError(f'Create target already exists: {name}; use modify with source ranges')
            if any(q.exists() and not q.is_dir() for q in p.parents if self.root in q.parents):
                raise ValueError(f'Create target parent is not a directory: {name}')
            return p
        if not p.exists() or (file and not p.is_file()):
            raise ValueError(f'Source path does not exist or is not a file: {name}. '
                             'For a required NEW file, save operation=create with reason and no line range; do not drop it.')
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
        data = self.load()
        if data.get('exploration_policy') == EXPLORATION_POLICY:
            if data.get('plan_contract') == 'complete_strategy_v1':
                complete_strategy()['update_requirements'](data, record)
            if not nonempty(record.get('notes')):
                raise ValueError('Supply brief notes on current source evidence and next steps')
            if record.get('next_action', 'continue') not in ('continue', 'switch', 'finalize'):
                raise ValueError('next_action must be continue/switch/finalize')
            hypothesis_queue()['checkpoint'](data, record, self.control())
            data['candidate_policy'] = SIMPLE_POLICY
            self.write(data)
            return 'Hypotheses updated independently; omitted branches preserved.', {
                'ok': True, 'next_action': record.get('next_action', 'continue'),
                **hypothesis_queue()['state'](data)}
        if operation == 'update':
            record = {**data.get('investigation', {}), **record}
        if not nonempty(record.get('notes')) and not record.get('requirements'):
            raise ValueError('Supply notes with findings, open alternatives and next steps at the TOP level')
        action = record.get('next_action', 'continue')
        if action not in ('continue', 'switch', 'finalize'):
            raise ValueError('next_action must be continue/switch/finalize')
        data.update(investigation=record, candidate_policy=SIMPLE_POLICY)
        self.write(data)
        return 'Investigation notes saved.', {'ok': True, 'active_hypothesis': record.get('active_hypothesis'),
                'next_action': action, 'candidate_count': len(data['candidates'])}

    def candidate(self, request):
        data = self.load()
        operation = request.get('operation', 'save')
        if operation == 'save':
            supplied = request.get('candidate', {})
            # One object replaces both parts together; no separate hypothesis/version state.
            independent = data.get('exploration_policy') == EXPLORATION_POLICY
            keys = ('id', 'hypothesis_id', 'files_to_modify') if independent else ('id', 'files_to_modify', 'unresolved_questions')
            whole = data.get('plan_contract') == 'complete_strategy_v1'
            if whole:
                if not independent:
                    raise ValueError('Complete strategies require independent hypothesis exploration')
                keys += ('implementation_outline', 'requirement_coverage', 'dependency_review')
            c = {key: copy.deepcopy(supplied[key]) for key in keys if key in supplied}
            c['schema_version'] = SIMPLE_SCHEMA
            if independent:
                c.update(planning_mode='locations_only_v1', unresolved_questions=[])
                hypothesis_queue()['bind_candidate'](data, c, self.control())
            if whole:
                c.update(planning_mode='complete_strategy_v1',
                         requirements=copy.deepcopy(data['investigation'].get('requirements')))
            normalizations = normalize_question_placement(c)
            sources = {}
            for f in c.get('files_to_modify', []):
                if f.get('operation') == 'create':
                    self.source(f.get('file_path', ''), create=True)
                    for m in f.get('modifications', []):
                        m['current_code'] = ''
                    continue
                p = self.source(f.get('file_path', ''), file=True)
                if p.stat().st_size > 2_000_000:
                    raise ValueError('Source file too large')
                lines = p.read_text().splitlines()
                sources[f['file_path']] = lines
                for m in f.get('modifications', []):
                    loc = m.get('location', {});start, end = loc.get('start_line'), loc.get('end_line')
                    if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
                        raise ValueError(f'Invalid range for {f["file_path"]}; file has {len(lines)} lines')
                    m['current_code'] = '\n'.join(lines[start-1:end]) + '\n'
            # Check references/unique IDs before remapping so malformed requests
            # cannot silently lose responsibilities during overlap normalization.
            # Exact duplicates are also merged, provided their IDs are distinct.
            validate_candidate_contract(c, allow_duplicate_ranges=True)
            if whole:
                complete_strategy()['validate_plan'](c, source=self.source)
            aliases = merge_overlapping_locations(c)
            warnings = []
            for f in c['files_to_modify']:
                if f.get('operation') == 'create':
                    continue
                for m in f['modifications']:
                    start, end = m['location']['start_line'], m['location']['end_line']
                    m['current_code'] = '\n'.join(sources[f['file_path']][start-1:end]) + '\n'
                    if end - start + 1 > 200:
                        warnings.append(f'{m["id"]}: {f["file_path"]}:{start}-{end} spans {end-start+1} lines; '
                                        'narrow to cooperating edits if possible, but retain necessary coverage.')
            validate_candidate_contract(c)
            notices = reference_notices(c)
            previous = next((x for x in data['candidates'] if x['id'] == c['id']), None)
            data['candidates'] = [x for x in data['candidates'] if x['id'] != c['id']] + [c]
            data['candidate_policy'] = SIMPLE_POLICY
            if independent:
                hypothesis_queue()['validate'](data, allow_open=True)
            self.write(data)
            detail = f' Merged IDs: {json.dumps(aliases)}.' if aliases else ''
            detail += ''.join('\nNormalized: ' + n for n in normalizations)
            detail += ''.join('\nRange notice: ' + w for w in warnings)
            detail += ''.join('\nCoverage notice: ' + n['message'] for n in notices)
            label = 'complete location plan' if independent else 'locations and checks'
            return f'Saved {c["id"]}: {label} (structural validation only).' + detail, {
                'ok': True, 'candidate_count': len(data['candidates']), 'changed': previous != c,
                'merged_location_ids': aliases, 'warnings': warnings,
                'normalizations': normalizations, 'reference_notices': notices,
                **(hypothesis_queue()['state'](data) if independent else {})}
        if operation == 'remove':
            if not nonempty(request.get('reason')):
                raise ValueError('Explain concrete evidence showing this candidate cannot work')
            previous = next((x for x in data['candidates'] if x['id'] == request.get('id')), None)
            if previous is None:
                raise ValueError('Unknown candidate id')
            if data.get('exploration_policy') == EXPLORATION_POLICY:
                hypothesis_queue()['remove_candidate'](data, previous, request)
            data['candidates'].remove(previous)
            data.setdefault('candidate_rejections', []).append({'candidate': previous, 'reason': request['reason']})
            self.write(data)
            return 'Candidate removed; rejection reason recorded.', {'ok': True, 'candidate_count': len(data['candidates']), 'changed': True,
                **(hypothesis_queue()['state'](data) if data.get('exploration_policy') == EXPLORATION_POLICY else {})}
        if operation == 'get':
            c = next((x for x in data['candidates'] if x['id'] == request.get('id')), None)
            if c is None:
                raise ValueError('Unknown candidate id')
            c = copy.deepcopy(c)
            for f in c['files_to_modify']:
                for m in f['modifications']:
                    m.pop('current_code', None)
            return json.dumps(c, ensure_ascii=False), {'ok': True, 'candidate_count': len(data['candidates'])}
        if operation in ('list', 'validate'):
            validate_retention(data) if operation == 'validate' else None
            return json.dumps([{'id': c['id'], 'files': [f['file_path'] for f in c['files_to_modify']],
                                'verification_points': len(c['unresolved_questions'])} for c in data['candidates']]), {
                                    'ok': True, 'candidate_count': len(data['candidates'])}
        raise ValueError('Use save/get/list/validate/remove; no compile, version-sync or review step is needed')

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
    diagnostics = []
    try:
        if len(sys.argv) != 2:
            raise ValueError('Supply one JSON request argument (location_abort takes a plain reason)')
        root = registry.get('ROOT')
        if not root:
            raise ValueError('Repository ROOT unavailable; environment initialization failed')
        store = Store(root)
        method = getattr(store, name.removeprefix('location_'))
        payload, normalized = ((sys.argv[1], False) if name == 'location_abort'
                               else decode_request(sys.argv[1], diagnostics=diagnostics))
        text, event = method(payload)
        if normalized:
            print('Request formatting normalized; supplied content preserved: ' + '; '.join(diagnostics))
            event['request_normalizations'] = diagnostics
        print(text)
        print('LOCATION_STATE ' + json.dumps(event))
    except (ValueError, KeyError, TypeError, AttributeError, OSError, UnicodeError, re.error) as exc:
        print('Localization tool error: ' + str(exc))
        print('LOCATION_STATE ' + json.dumps({'ok': False, 'request_normalizations': diagnostics}))
        sys.exit(1)
