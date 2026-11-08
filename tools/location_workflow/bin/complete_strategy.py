"""Whole-strategy coverage bookkeeping, using only issue/current-source evidence.

These checks enforce references and explicit obligations, not semantic correctness.
They intentionally contain no benchmark IDs, repository paths or reference fixes.
"""
import copy

MODE = 'complete_strategy_v1'
AREAS = ('entrypoints', 'state_and_defaults', 'consumers_and_contracts',
         'persistence_and_serialization', 'dependencies_and_registration',
         'compatibility_and_errors')


def text(value):
    return isinstance(value, str) and bool(value.strip())


def requirements(items):
    if not isinstance(items, list) or not items:
        raise ValueError('Register shared requirements: [{"id":"R1","text":"Required behavior"}]')
    ids = set()
    for item in items:
        if (not isinstance(item, dict) or not text(item.get('id')) or
                item['id'] in ids or not text(item.get('text'))):
            raise ValueError('Requirements need unique ids and non-empty text')
        ids.add(item['id'])
    return ids


def update_requirements(data, record):
    """Omitted requirements survive; an existing obligation cannot be weakened."""
    old = copy.deepcopy(data.get('investigation', {}).get('requirements', []))
    if 'requirements' in record:
        requirements(record['requirements'])
        by_id = {r['id']: r for r in old}
        for item in record['requirements']:
            if item['id'] in by_id and item != by_id[item['id']]:
                raise ValueError('Preserve registered requirement text; add newly discovered obligations with new ids')
            by_id[item['id']] = copy.deepcopy(item)
        old = list(by_id.values())
    requirements(old)
    record['requirements'] = old


def refs(item, known):
    values = item.get('location_ids')
    if (not isinstance(values, list) or any(not text(v) for v in values) or
            len(set(values)) != len(values) or set(values) - known):
        raise ValueError('Coverage location_ids must reference THIS candidate, never another strategy')
    return set(values)


def evidence(items, source=None):
    if not isinstance(items, list) or not items:
        raise ValueError('An unchanged claim needs current-source evidence with file_path/start_line/end_line')
    for e in items:
        if not isinstance(e, dict) or not text(e.get('file_path')):
            raise ValueError('Evidence needs a current-source file_path')
        from pathlib import PurePosixPath
        path = PurePosixPath(e['file_path'])
        if path.is_absolute() or '..' in path.parts or any(
                part in ('.git', '.hg', '.svn', '.venv', 'node_modules') for part in path.parts):
            raise ValueError('Evidence must use repository-relative current-source paths')
        start, end = e.get('start_line'), e.get('end_line')
        if type(start) is not int or type(end) is not int or not 1 <= start <= end:
            raise ValueError('Evidence needs an actual source range')
        if source:
            p = source(e['file_path'], file=True)
            if p.stat().st_size > 2_000_000 or end > len(p.read_text().splitlines()):
                raise ValueError('Evidence range exceeds current source')


def validate_plan(candidate, shared=None, source=None):
    if candidate.get('planning_mode') != MODE:
        raise ValueError('This run requires a complete_strategy_v1 candidate')
    reqs = candidate.get('requirements')
    wanted = requirements(reqs)
    if shared is not None and reqs != shared:
        raise ValueError('Shared requirements changed: refresh this candidate against ALL current requirements')
    if not text(candidate.get('implementation_outline')):
        raise ValueError('Explain the complete mechanism in implementation_outline, including cooperating changes')
    locations = {m['id'] for f in candidate['files_to_modify'] for m in f['modifications']}
    for f in candidate['files_to_modify']:
        for m in f['modifications']:
            if not text(m.get('reason')):
                raise ValueError('Every location needs its intended behavioral change, not just a file/function name')
    coverage = candidate.get('requirement_coverage')
    if not isinstance(coverage, list):
        raise ValueError('Supply requirement_coverage for every shared requirement')
    seen, changed = set(), set()
    for row in coverage:
        if not isinstance(row, dict):
            raise ValueError('Each coverage record must be an object')
        rid = row.get('requirement_id')
        if rid not in wanted or rid in seen:
            raise ValueError('Use each shared requirement_id exactly once')
        seen.add(rid)
        ids = refs(row, locations)
        if not text(row.get('explanation')):
            raise ValueError('Explain HOW this strategy satisfies each requirement')
        if row.get('status') == 'change':
            if not ids:
                raise ValueError('A change requirement needs concrete location_ids in this candidate')
            changed.update(ids)
        elif row.get('status') == 'already_satisfied':
            if ids:
                raise ValueError('Already-satisfied behavior uses source evidence, not modification locations')
            try:
                evidence(row.get('evidence'), source)
            except ValueError as exc:
                raise ValueError(f'requirement_coverage[{rid}].evidence: {exc}') from exc
        else:
            raise ValueError(f'Unresolved requirements cannot be saved as complete: requirement_coverage[{rid}].status='
                             f'{row.get("status")!r}. Use change with concrete edits or already_satisfied with '
                             'current-source evidence only after resolving this obligation; otherwise keep exploring.')
    if seen != wanted:
        raise ValueError('Incomplete strategy; missing requirements: ' + ', '.join(sorted(wanted-seen)))
    if locations - changed:
        raise ValueError('Map every planned edit to its requirement, including cooperating edits: ' +
                         ', '.join(sorted(locations-changed)))
    review = candidate.get('dependency_review')
    if not isinstance(review, list):
        raise ValueError('Supply dependency_review for: ' + ', '.join(AREAS))
    seen = set()
    for row in review:
        if not isinstance(row, dict) or row.get('area') not in AREAS or row['area'] in seen:
            raise ValueError('Review each dependency area once: ' + ', '.join(AREAS))
        seen.add(row['area'])
        ids = refs(row, locations)
        if not text(row.get('explanation')):
            raise ValueError('Explain the dependency decision; not_applicable also needs a reason')
        status = row.get('status')
        if status == 'change':
            if not ids:
                raise ValueError('Required cooperating changes must appear in this candidate')
        elif status in ('unchanged', 'not_applicable'):
            if ids:
                raise ValueError('Unchanged/not_applicable dependencies cannot reference planned modifications')
            if status == 'unchanged' or row.get('evidence'):
                try:
                    evidence(row.get('evidence'), source)
                except ValueError as exc:
                    raise ValueError(f'dependency_review[{row["area"]}].evidence: {exc}') from exc
        else:
            raise ValueError('Resolve dependency location choices before saving: change/unchanged/not_applicable')
    if seen != set(AREAS):
        raise ValueError('Missing dependency areas: ' + ', '.join(sorted(set(AREAS)-seen)))


def remap(candidate, aliases):
    for name in ('requirement_coverage', 'dependency_review'):
        for row in candidate.get(name, []):
            row['location_ids'] = list(dict.fromkeys(aliases.get(x, x) for x in row['location_ids']))


def validate_all(data):
    shared = data.get('investigation', {}).get('requirements')
    requirements(shared)
    for candidate in data['candidates']:
        validate_plan(candidate, shared)
