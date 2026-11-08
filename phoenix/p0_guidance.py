"""Author instructions used before the original P0 submission."""

IMPLEMENTATION_GUIDANCE = """
The localization/summary is an UNVERIFIED proposal. It can omit cooperating edits,
misread a requirement, contain contradictions, or describe an incorrect API.
The ORIGINAL issue description and requirements take precedence over this proposal
and over reviewer claims. Before editing, inspect the relevant current source and
its callers/consumers; briefly identify which decisions you keep or correct and why.
Do not repeat the entire localization or blindly implement every proposed edit.
You may correct the mechanism and expand the file set when source/issue evidence
requires it. Preserve sound parts and implement one complete solution to the issue.
Validate behavior with focused existing tests or temporary reproductions, including
the affected public entrypoints and boundary paths. Separate observed code failures
from environment/network failures; never describe an unrun check as passed. Remove
temporary reproduction files from the submitted diff and do not modify test files.
Before submitting, inspect the ACTUAL diff against the original requirements:
each required behavior needs an implemented change or verified existing support;
plan prose, TODOs, and a review approval are not implementations or test results.
A private SAME-model reviewer will inspect your submitted diff and actual source.
If it finds a concrete defect, fix that defect and check its affected behavior;
keep unrelated correct work. If feedback conflicts with the issue/source, explain
the evidence instead of following it blindly. At most two patch reviews are run,

within the SHARED 200-call budget across this task's candidates, summary and final.
The stage budget will be stated at runtime. Reserve 40 calls in the final stage:
10 for initial review, 25 for one focused repair, and 5 for recheck. Submit the first
complete patch before the stated generation allowance ends. Unused earlier-stage
allowances may be transferred to the final stage; no charged call is reset.
When requirements share state or event ordering, verify them together in a concrete
scenario before submission. A change fixing one requirement must not contradict a
related one. The reviewer will record incremental evidence, check proposed fixes
jointly, and request at most one focused repair followed by one delta recheck.
Tests must execute actual repository code and report real status; log formatting
must not mask a failed or unrun check. Unknown validation remains explicitly unknown.
"""
