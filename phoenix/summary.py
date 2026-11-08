"""The original TTS comparison prompt and patch compaction policy."""
import hashlib
import json
from pathlib import Path
import re
import time

import requests

from phoenix.prompts import PromptBuilder
from phoenix.p0_guidance import SUMMARY_GUIDANCE
from phoenix.settings import PROFILES, write_json

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
    write_json(directory / "request.json", payload)
    attempts, usage = 0, dict(prompt_tokens=0, completion_tokens=0, total_tokens=0)
    transport_limit = 1 if shared_budget else (3 if kimi else 5)
    format_limit = 1 if shared_budget else 3
    session = requests.Session()
    session.trust_env = False
    try:
        for _ in range(format_limit):
            response = None
            for retry in range(transport_limit):
                attempts += 1
                try:
                    result = session.post(settings["api_base"].rstrip("/") + "/chat/completions",
                        headers={"Authorization": "Bearer " + key}, json=payload, timeout=timeout)
                    if not result.ok:
                        raise RuntimeError(f"Summary API HTTP {result.status_code}")
                    response = result.json()
                    if "data" in response and "response" in response["data"]:
                        response = response["data"]["response"]
                    write_json(directory / f"response-{attempts}.json", response)
                    for k in usage:
                        usage[k] += response.get("usage", {}).get(k, 0) or 0
                    break
                except (requests.RequestException, ValueError, RuntimeError) as exc:
                    write_json(directory / f"transport-error-{attempts}.json", {"error_type": type(exc).__name__, "attempt": attempts})
                    if retry + 1 == transport_limit:
                        raise RuntimeError("Summary request failed after bounded attempts; see stage artifacts") from None
                    time.sleep(2)
            choice = response["choices"][0]
            answer = choice["message"].get("content") or ""
            match = re.search(r"\*\*BEST_SAMPLE:\*\*\s*(\d+)", answer)
            valid = (match and 1 <= int(match[1]) <= len(inputs) and "**REASONING:**" in answer
                     and "**INSIGHTS FOR NEXT ATTEMPT:**" in answer)
            if p0:
                # The scored P0 run passed any nonempty same-model summary to
                # its final author; TTS's separate format gate was not used.
                valid = bool(answer.strip())
            if kimi and choice.get("finish_reason") == "length":
                valid = False
            if valid:
                (directory / "summary.txt").write_text(answer)
                return answer
            (directory / f"invalid-summary-{attempts}.txt").write_text(answer)
        raise RuntimeError("Summary format validation failed; final generation is blocked")
    finally:
        write_json(directory / "usage.json", {"api_attempts": attempts, **usage})
        session.close()
