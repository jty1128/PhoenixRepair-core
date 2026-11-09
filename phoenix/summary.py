"""The original TTS comparison prompt and patch compaction policy."""
import hashlib
from pathlib import Path
import re
import time
import os

import requests

from phoenix.prompts import PromptBuilder
from phoenix.p0_guidance import SUMMARY_GUIDANCE
from phoenix.settings import PROFILES, write_json
from phoenix.run_state import bind, read, digest

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def patch_for_prompt(path, limit=180_000):
    """Keep ordinary diffs intact and compact oversized generated files for comparison."""
    original = Path(path).read_text()
    if len(original) <= limit:
        return original, dict(original_chars=len(original), prompt_chars=len(original), compacted=False)
    sections = re.split(r'(?=^diff --git )', original, flags=re.MULTILINE)
    budget = limit - 2_000
    selected = []
    for section in sections:
        if not section:
            continue
        if len(section) > 40_000:
            header = section.splitlines()[0]
            digest = hashlib.sha256(section.encode()).hexdigest()
            section = (section[:6_000] + '\n[Oversized diff section compacted: ' + header +
                       '; SHA256=' + digest + '; omitted ' + str(len(section) - 8_000) +
                       ' characters]\n' + section[-2_000:])
        if len(section) > budget:
            header = section.splitlines()[0]
            section = ('[Diff section omitted from comparison prompt: ' + header +
                       '; SHA256=' + hashlib.sha256(section.encode()).hexdigest() + ']\n')
        selected.append(section)
        budget -= len(section)
    preview = ('[Full patch SHA256=' + sha(path) + '; original characters=' +
               str(len(original)) + '; oversized sections compacted for comparison]\n' +
               ''.join(selected))
    return preview, dict(original_chars=len(original), prompt_chars=len(preview), compacted=True)


def validate_tts_summary(text, num_samples=3):
    """Historical TTS gate, accepting plain/bold headings and bracketed indices."""
    normalized = text.replace('**', '').replace('__', '')
    match = re.search(r'(?im)^\s*BEST_SAMPLE\s*:\s*\[?(\d+)\]?(?:\s|$)', normalized)
    if (not match or match.group(1) not in {str(i) for i in range(1, num_samples + 1)} or
            'REASONING:' not in normalized or 'INSIGHTS FOR NEXT ATTEMPT:' not in normalized):
        raise ValueError('Summary is missing required sections or a valid sample index')
    if len(text.strip()) < 100:
        raise ValueError('Summary is unexpectedly short')
    return int(match.group(1))


def summarize(task, candidates, settings, directory, key, *, shared_budget=False):
    directory.mkdir(parents=True, exist_ok=True)
    p0 = settings["method"] == "our_method"
    if p0 and not shared_budget:
        raise ValueError("P0 summary must use the shared one-call budget")
    inputs = []
    for index, row in enumerate(candidates, 1):
        patch = Path(row["patch"])
        if not patch.is_file() or not patch.read_text().strip():
            raise ValueError("Summary requires every candidate patch to be present and nonempty")
        if p0:
            preview = patch.read_text()
            metadata = dict(original_chars=len(preview), prompt_chars=len(preview), compacted=False)
        else:
            preview, metadata = patch_for_prompt(patch)
        inputs.append(dict(index=index, trajectory=[None] * row["steps"], submission=preview,
                           exit_status="submitted" if p0 else row["exit_status"], patch_sha256=sha(patch), preview=metadata))
    prompt = PromptBuilder()._build_comparison_prompt(task["problem_statement"], inputs, len(inputs))
    if p0:
        prompt += SUMMARY_GUIDANCE
    (directory / "prompt.txt").write_text(prompt)
    write_json(directory / "inputs.json", [{k: v for k, v in row.items() if k != "submission"} for row in inputs])
    kimi = settings["model"] == "kimi-k2.7-code"
    payload = dict(model=settings["model"], messages=[{"role": "user", "content": prompt}], temperature=0.7,
                   top_p=1.0, max_tokens=32768 if kimi else 8192, enable_thinking=kimi)
    if kimi:
        payload["preserve_thinking"] = True
    if p0:
        payload.update(temperature=0.0, max_tokens=PROFILES[settings["model"]]["max_tokens"])
    timeout = (30, PROFILES[settings["model"]]["timeout"]) if p0 else (240 if kimi else 120)
    bind(directory / "identity.json", {"payload": payload, "api_base": settings["api_base"],
                                      "shared_budget": shared_budget})
    write_json(directory / "request.json", payload)
    state = read(directory / "state.json") or {
        "attempts": 0, "format_attempt": 0, "transport_attempt": 0, "phase": "ready",
        "usage": dict(prompt_tokens=0, completion_tokens=0, total_tokens=0),
        "usage_applied": [], "unknown_requests": []}
    receipt = read(directory / "result.json")
    if receipt.get("status") == "completed":
        if digest(directory / "summary.txt") != receipt["summary_sha256"]:
            raise ValueError("Completed summary changed; refusing to regenerate or continue")
        return (directory / "summary.txt").read_text()
    if state["phase"] == "failed":
        raise RuntimeError("Summary exhausted its original bounded attempts; no budget reset on resume")
    transport_limit = 1 if shared_budget else (3 if kimi else 5)
    format_limit = 1 if shared_budget else 3
    session = requests.Session()
    session.trust_env = False
    def persist():
        write_json(directory / "state.json", state)
        write_json(directory / "usage.json", {"api_attempts": state["attempts"], **state["usage"],
                                            "unknown_requests": state["unknown_requests"]})

    def transport_failure():
        if state["transport_attempt"] >= transport_limit:
            if p0 or kimi:
                state["phase"] = "failed"
            else:
                state["format_attempt"] += 1
                state["transport_attempt"] = 0
                state["phase"] = "ready" if state["format_attempt"] < format_limit else "failed"
        else:
            state["phase"] = "ready"
        persist()

    try:
        while state["format_attempt"] < format_limit and state["phase"] != "failed":
            response_path = directory / f"response-{state['attempts']}.json"
            if state["phase"] == "inflight":
                # A response persisted before a crash is consumed exactly once.
                # An unknown provider outcome remains charged, never refunded.
                if response_path.exists():
                    state["phase"] = "response"
                else:
                    state["unknown_requests"].append(state["attempts"])
                    transport_failure()
                    continue
            if state["phase"] == "ready":
                if p0:
                    from sweagent.agent.request_pacing import wait_for_request
                    account = os.environ.get("PHOENIX_ACCOUNT_ID") or "env-" + hashlib.sha256(key.encode()).hexdigest()[:16]
                    wait_for_request(account, "openai/" + settings["model"], 2)
                state["attempts"] += 1
                state["transport_attempt"] += 1
                state["phase"] = "inflight"
                persist()  # Charge before transmission, including DNS/timeouts.
                response_path = directory / f"response-{state['attempts']}.json"
                try:
                    result = session.post(settings["api_base"].rstrip("/") + "/chat/completions",
                        headers={"Authorization": "Bearer " + key}, json=payload, timeout=timeout)
                    if not result.ok:
                        raise RuntimeError(f"Summary API HTTP {result.status_code}")
                    response = result.json()
                    if "data" in response and "response" in response["data"]:
                        response = response["data"]["response"]
                    write_json(response_path, response)
                    state["phase"] = "response"
                    persist()
                except (requests.RequestException, ValueError, RuntimeError, KeyError, IndexError, TypeError, AttributeError) as exc:
                    write_json(directory / f"transport-error-{state['attempts']}.json",
                               {"error_type": type(exc).__name__, "attempt": state["attempts"]})
                    transport_failure()
                    if state["phase"] == "ready" and state["transport_attempt"]:
                        time.sleep(2)
                    continue
            try:
                response = read(response_path)
                if state["attempts"] not in state["usage_applied"]:
                    for k in state["usage"]:
                        state["usage"][k] += response.get("usage", {}).get(k, 0) or 0
                    state["usage_applied"].append(state["attempts"])
                    persist()
                choice = response["choices"][0]
                answer = choice["message"].get("content") or ""
                if not isinstance(answer, str):
                    raise ValueError("Summary content must be text")
            except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                transport_failure()
                continue
            if p0:
                # The scored P0 run passed any nonempty same-model summary to
                # its final author; TTS's separate format gate was not used.
                valid = bool(answer.strip())
            elif kimi:
                match = re.search(r"\*\*BEST_SAMPLE:\*\*\s*(\d+)", answer)
                valid = (match and 1 <= int(match[1]) <= len(inputs) and "**REASONING:**" in answer
                         and "**INSIGHTS FOR NEXT ATTEMPT:**" in answer)
            else:
                try:
                    validate_tts_summary(answer, len(inputs))
                    valid = True
                except ValueError:
                    valid = False
            if kimi and choice.get("finish_reason") == "length":
                valid = False
            if valid:
                (directory / "summary.txt").write_text(answer)
                write_json(directory / "result.json", {"status": "completed",
                    "summary_sha256": digest(directory / "summary.txt"), "model": settings["model"],
                    "api_attempts": state["attempts"], "budget_reset": False})
                state["phase"] = "completed"
                persist()
                return answer
            (directory / f"invalid-summary-{state['attempts']}.txt").write_text(answer)
            state["format_attempt"] += 1
            state["transport_attempt"] = 0
            state["phase"] = "ready"
            persist()
        state["phase"] = "failed"
        raise RuntimeError("Summary request/format failed after bounded attempts; final generation is blocked")
    finally:
        persist()
        session.close()
