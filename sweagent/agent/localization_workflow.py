"""Budgeted localization control, independent of repair agents and model reasoning."""
import json

MARKER = 'LOCATION_STATE '


class LocalizationWorkflow:
    def __init__(self, *, independent_hypotheses=False, complete_strategies=False):
        if complete_strategies and not independent_hypotheses:
            raise ValueError('Complete strategies require independent hypothesis exploration')
        self.complete_strategies = complete_strategies
        self.independent_hypotheses = independent_hypotheses
        self.hypotheses = []
        self.active_since = 0
        self.stalled_hypothesis = None
        self.last_checkpoint = 0
        self.last_progress_review = None
        self.reviewed_hypothesis = None
        self.seen_outputs = set()
        self.new_outputs = 0
        self.candidate_revision = 0
        self.reviewed_revision = 0
        self.candidate_count = 0
        self.active_hypothesis = None
        self.consolidating = False
        self.last_progress = True
        self.last_notice = None

    def control(self, step, limit):
        if self.independent_hypotheses:
            result = self._independent_control(step, limit)
            if self.complete_strategies:
                result['plan_contract'] = 'complete_strategy_v1'
                result['allow_discovery_checkpoint'] = not self.hypotheses and result['phase'] == 'map'
                result['instructions'] += (
                    ' Register ALL issue requirements shared across strategies. For EACH strategy, '
                    'trace every requirement and its cooperating edits independently. Saving requires '
                    'implementation_outline, requirement_coverage and dependency_review, not tests or code. '
                    'No partial strategy may be presented as complete; a large source range is not a behavioral plan. '
                    'When requirements are added, refresh previously saved candidates before submission.')
                if result['allow_discovery_checkpoint']:
                    result['instructions'] += (
                        ' During initial discovery a notes-only location_checkpoint is valid: record what was '
                        'actually inspected and the next focused source read, leaving unknown hypotheses open. '
                        'This progress checkpoint reopens source reads; it is NOT a completed strategy. '
                        'Extract behavioral requirements from the ORIGINAL issue; they do not require implementation '
                        'source proof. Register grounded whole-repair hypotheses once supported. Do not invent '
                        'a hypothesis merely to pass a checkpoint. Avoid repeatedly listing nested directories '
                        'when an issue path or recursive source search can reach the relevant files.')
            return result
        # Fixed boundaries for the 150-step policy; a smaller cap still reserves
        # its last 20 calls for submission rather than permitting an overrun.
        submit_at = min(131, max(1, limit - 19))
        consolidate_at = min(111, max(1, submit_at - 20))
        phase = ('submit' if step >= submit_at else 'consolidate'
                 if step >= consolidate_at or self.consolidating else
                 'map' if step <= 25 else 'explore')
        due = phase != 'submit' and ((step >= 25 and self.last_checkpoint < 25)
                                     or (step > 25 and step - self.last_checkpoint >= 8))
        names = ['location_checkpoint', 'location_candidate', 'location_abort', 'analysis_complete']
        if due:
            names = ['location_checkpoint', 'location_abort', 'analysis_complete']
        elif phase != 'submit':
            names.append('location_read')
        return {'step': step, 'limit': limit, 'remaining_including_this_step': limit-step+1,
                'phase': phase, 'checkpoint_required': due, 'allowed_tools': names,
                'candidate_count': self.candidate_count,
                'new_read_outputs_since_checkpoint': self.new_outputs,
                'candidate_changed_since_checkpoint': self.candidate_revision != self.reviewed_revision,
                'instructions': (
                    'Only correct/save/remove candidates, review records, submit or explicitly abort. No source searches. '
                    'Retain ALL unrefuted strategies as complete candidates. Choices changing files/locations need '
                    'separate candidates, not just unresolved_questions; exclude only with concrete impossibility evidence. '
                    'Ensure every candidate contains the full cooperating location set and verification points '
                    'for those locations, including create targets and necessary body/caller/import edits. '
                    'Checks must preserve required outcomes, not reopen them as choices. '
                    'Save both together and submit; no separate review is needed.'
                    if phase == 'submit' else
                    'Save a short notes checkpoint NOW: new facts, remaining location alternatives and next steps. '
                    'If no progress on this branch, switch to another grounded alternative using '
                    'next_action=switch and an active_hypothesis label. For explicit repair locations, '
                    'finalize once their necessary cooperating edits are covered; no broad search is needed. '
                    'For open repair positions, finalize early only when grounded alternatives are exhausted, '
                    'not just because one candidate was saved. '
                    'Retain all plausible complete plans; uncertainty is not a rejection reason.'
                    if due else
                    'Complete existing plans and resolve necessary location gaps; do not introduce new hypotheses. '
                    'Keep plausible plans even when implementation is needed to check them.'
                    if phase == 'consolidate' else
                    'If the issue explicitly identifies intended repair locations, check them and necessary '
                    'cooperating edits, save and finish promptly; avoid broad alternatives searches. Otherwise '
                    'map independent repair possibilities from the original issue and shared facts, maximizing '
                    'grounded complete location combinations within the budget without a quota. '
                    'Save complete plans when available; '
                    'record remaining branches in brief notes by step 25.'
                    if phase == 'map' else
                    'Explicit-location tasks can finish once prescribed edits and cooperating locations are covered. '
                    'For open-location tasks, check the active hypothesis against the original issue and shared facts. Save each complete '
                    'plausible plan immediately, then explore other grounded branches and preserve implementation '
                    'uncertainties. Completeness is a saving constraint, not the exploration stopping rule. '
                    'Review progress every 8 steps; do not just refine the first plan or discard alternatives '
                    'because a preferred plan exists.')}

    def _independent_control(self, step, limit):
        submit_at = max(1, limit - 19)
        consolidate_at = max(1, submit_at - 20)
        phase = ('submit' if step >= submit_at else 'consolidate' if step >= consolidate_at
                 else 'explore' if self.hypotheses else 'map')
        pending = [h['id'] for h in self.hypotheses if h['status'] == 'open']
        others = [h for h in pending if h != self.active_hypothesis]
        active_finished = bool(others) and self.active_hypothesis not in pending
        switch = bool(others) and (active_finished or step - self.active_since >= 24 or
                                  self.stalled_hypothesis == self.active_hypothesis)
        early = phase in ('map', 'explore')
        due = early and ((not self.hypotheses and step-self.last_checkpoint >= min(8, max(1, limit // 4))) or
                         (bool(self.hypotheses) and step - self.last_checkpoint >= 8) or switch)
        names = ['location_checkpoint', 'location_abort']
        if not due:
            if phase != 'submit':
                names.append('location_read')
            if self.hypotheses:
                names.append('location_candidate')
        if self.candidate_count and self.hypotheses and (not pending or not early):
            names.append('analysis_complete')
        return {'step': step, 'limit': limit, 'remaining_including_this_step': limit-step+1,
                'phase': phase, 'checkpoint_required': due, 'allowed_tools': names,
                'exploration_policy': 'independent_hypotheses_v1',
                'hypotheses': self.hypotheses, 'open_hypotheses': pending,
                'active_hypothesis': self.active_hypothesis, 'switch_required': switch and early,
                'candidate_count': self.candidate_count,
                'instructions': (
                    'Submit complete saved plans. Keep unfinished hypotheses explicitly open and report budget_limited; '
                    'never fabricate rejection to finish. No new source reads, test plans or patches.'
                    if phase == 'submit' else
                    'Complete existing hypotheses separately using known locations and focused reads; no new hypotheses. '
                    'If budget prevents completion, submit saved complete plans with unfinished branches recorded honestly.'
                    if phase == 'consolidate' else
                    'Switch via location_checkpoint to another open hypothesis NOW. A completed or stalled branch '
                    'must not consume the remaining branches\' exploration budget.'
                    if switch else
                    'Checkpoint independent alternatives from the ORIGINAL issue and shared current-source facts, '
                    'before saving any candidate. Each idea must describe a possible WHOLE repair mechanism, '
                    'not one required edit. If only one is grounded, explain why; never invent alternatives for a quota.'
                    if not self.hypotheses else
                    'Investigate the active hypothesis on its own merits, then save its COMPLETE cooperating location set '
                    'with hypothesis_id. Short intended edits only; implementation and tests belong to patch generation. '
                    'After saving, switch to remaining open hypotheses. Do not derive all branches from the first plan, '
                    'merge branches into one candidate, or stop because a file/function was named by the issue. '
                    'Reject only with a concrete source/requirement contradiction to this complete alternative; '
                    'preference, uncertainty, unverified assumptions and budget pressure are not proof.')}

    def observe(self, name, observation, step):
        event = None
        for line in observation.splitlines():
            if line.startswith(MARKER):
                try:
                    event = json.loads(line[len(MARKER):])
                except ValueError:
                    pass
        if not event or not event.get('ok'):
            return
        if self.independent_hypotheses and event.get('exploration_policy') == 'independent_hypotheses_v1':
            self.hypotheses = event.get('hypotheses', self.hypotheses)
            active = event.get('active_hypothesis')
            if active != self.active_hypothesis:
                self.active_since = step
                self.stalled_hypothesis = None
        self.candidate_count = event.get('candidate_count', self.candidate_count)
        if name == 'location_read':
            # Successful, non-empty source results only; errors/empty searches
            # and different commands producing identical content are not progress.
            digest = event.get('evidence_digest')
            if digest and digest not in self.seen_outputs:
                self.seen_outputs.add(digest)
                self.new_outputs += 1
        if name == 'location_candidate' and event.get('changed'):
            self.candidate_revision += 1
        if name == 'location_checkpoint':
            progress = self.new_outputs > 0 or self.candidate_revision != self.reviewed_revision
            next_hypothesis = event.get('active_hypothesis')
            switched = bool(next_hypothesis and next_hypothesis != self.reviewed_hypothesis)
            review_due = self.last_progress_review is not None and step - self.last_progress_review >= 8
            # Checkpoints can be adjacent (e.g. a voluntary save at 24 and the
            # mandatory map checkpoint at 25). They are not an 8-step evidence
            # window and must neither erase accumulated progress nor close
            # exploration. Never auto-consolidate during initial hypothesis mapping.
            if self.independent_hypotheses:
                if review_due and not progress and not switched:
                    self.stalled_hypothesis = next_hypothesis
            elif event.get('next_action') == 'finalize' or (step > 25 and review_due and not progress and not switched):
                self.consolidating = True
            self.last_progress = progress
            self.active_hypothesis = next_hypothesis
            self.last_checkpoint = step
            if self.last_progress_review is None or review_due:
                self.last_progress_review = step
                self.reviewed_hypothesis = next_hypothesis
                self.new_outputs = 0
                self.reviewed_revision = self.candidate_revision

    @staticmethod
    def message(control):
        return '[LOCALIZATION CONTROLLER — authoritative step budget]\n' + json.dumps(control, ensure_ascii=False)
