"""Recount retained HARD-51 usage without publishing private run artifacts.

Legacy model_stats are estimates, not provider usage. Missing billing records
remain unknown. P0 stops at first_submission_calls, before review/repair.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import tempfile
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

FX = Decimal("6.7168")  # CFETS USD/CNY central parity, 2026-11-06.
RATES = {
    "glm-5.2": (Decimal("8"), Decimal("2"), Decimal("28")),
    "qwen3.7-plus": (Decimal("2"), Decimal("0.4"), Decimal("8")),
}
RUNS = {
    "glm-5.2": [
        "pro-v2-hard51-glm52-tts-sample1_uniform-20261104",
        "pro-v2-hard51-glm52-tts-sample2-20261104",
        "pro-v2-hard51-glm52-tts-sample3-20261104",
        "pro-v2-hard51-glm52-tts-final-20261105",
    ],
    "qwen3.7-plus": [
        "pro-v2-hard51-qwen3.7-plus-no-thinking-50-20261104",
        "pro-v2-hard50-qwen37-tts-sample2-20261105",
        "pro-v2-hard50-qwen37-tts-sample3-20261105",
        "pro-v2-hard50-qwen37-tts-final-20261105",
    ],
}


def read(path):
    return json.loads(path.read_text())


def cost_cny(model, inputs, outputs, cached):
    if cached is None:
        return None
    if not 0 <= cached <= inputs or outputs < 0:
        raise ValueError("Invalid provider token counts")
    uncached_rate, cache_rate, output_rate = RATES[model]
    return ((inputs - cached) * uncached_rate + cached * cache_rate
            + outputs * output_rate) / Decimal(1_000_000)


def raw_usage(value):
    inputs, outputs = value["prompt_tokens"], value["completion_tokens"]
    if value.get("total_tokens", inputs + outputs) != inputs + outputs:
        raise ValueError("Provider total differs from input plus output")
    return inputs, outputs, value.get("prompt_tokens_details", {}).get("cached_tokens")


class RawTotals:
    def __init__(self, model):
        self.model = model
        self.counts = Counter()
        self.cost = Decimal(0)
        self.seen = set()

    def add(self, identity, inputs, outputs, cached):
        if identity in self.seen:
            raise ValueError("Duplicate provider response in one experiment")
        self.seen.add(identity)
        if min(inputs, outputs) < 0:
            raise ValueError("Negative token count")
        # These runs use the Beijing Qwen <=256K tier. Refuse silent repricing.
        if self.model == "qwen3.7-plus" and inputs > 256_000:
            raise ValueError("Qwen request exceeds the configured pricing tier")
        self.counts.update(requests=1, input_tokens=inputs, output_tokens=outputs)
        if cached is None:
            self.counts["cache_unknown_requests"] += 1
        else:
            self.cost += cost_cny(self.model, inputs, outputs, cached)
            self.counts["cached_input_tokens"] += cached

    def response(self, response):
        self.add("response:" + response["id"], *raw_usage(response["usage"]))

    def ledger_rows(self, rows, coverage):
        for row in rows:
            coverage["request_attempts"] += 1
            if not row["usage_available"]:
                if row["status"] == "success":
                    raise ValueError("Successful response has no provider usage")
                coverage["failed_requests_without_usage"] += 1
                coverage["no_usage_status_" + row["status"]] += 1
                coverage["failed_http_" + str(row["http_status"])] += 1
                continue
            if row["total_tokens"] != row["input_tokens"] + row["output_tokens"]:
                raise ValueError("Ledger total mismatch")
            self.add("ledger:" + row["id"], row["input_tokens"], row["output_tokens"], row["cached_input_tokens"])
            coverage["requests_with_usage"] += 1

    def merge(self, other):
        if self.seen & other.seen:
            raise ValueError("Request counted in more than one pipeline stage")
        self.seen.update(other.seen)
        self.counts.update(other.counts)
        self.cost += other.cost

    def result(self):
        return {
            **{k: self.counts[k] for k in (
                "requests", "input_tokens", "output_tokens",
                "cached_input_tokens", "cache_unknown_requests")},
            "total_tokens": self.counts["input_tokens"] + self.counts["output_tokens"],
            "known_usage_list_cost_cny": str(self.cost),
            "known_usage_list_cost_usd": str(self.cost / FX),
        }


def p0_rows(artifacts, ledger):
    root = artifacts / "old-location-evidence-supervised-full-20261107"
    locations = artifacts / "complete-strategies-20261106"
    plan = read(root / "plan.json")
    comparison = read(root / "paired-evaluation/comparison.json")
    results = {}
    for model in RUNS:
        totals = RawTotals(model)
        legacy = Counter()
        coverage = Counter()
        incomplete_tasks = set()
        tasks = [t for t in plan["tasks"] if t["model"] == model]
        assert len(tasks) == 51
        for task in tasks:
            src = locations / model / task["instance_id"]
            assert hashlib.sha256((src / "location_candidates.json").read_bytes()).hexdigest() == task["localization_sha256"]
            report = read(src / "report.json")
            local_sum = Counter()
            for line in (src / "trajectory.jsonl").read_text().splitlines():
                response = json.loads(line)["response"]
                totals.response(response)
                inputs, outputs, _ = raw_usage(response["usage"])
                local_sum.update(input=inputs, output=outputs, calls=1)
            assert local_sum["input"] == report["usage"]["prompt_tokens"]
            assert local_sum["output"] == report["usage"]["completion_tokens"]
            assert local_sum["calls"] == report["usage"]["api_calls"]
            legacy.update(input=local_sum["input"], output=local_sum["output"])
            coverage["localization_tasks_complete"] += 1
            coverage["localization_requests"] += local_sum["calls"]
        for job in plan["jobs"]:
            if job["model"] != model:
                continue
            stage = root / model / job["instance_id"] / job["stage"]
            result = read(stage / "result.json")
            if job["kind"] == "summary":
                response = read(stage / "response.json")
                totals.response(response)
                i, o, _ = raw_usage(response["usage"])
                legacy.update(input=i, output=o)
                coverage["summary_requests"] += 1
                continue
            supervision = read(stage / "supervision.json") if (stage / "supervision.json").exists() else {}
            expected_all = supervision.get("generation_calls", result.get("model_stats", {}).get("api_calls"))
            if job["is_final"]:
                checkpoint = read(stage / "P0.json")
                first = checkpoint["model_calls"]
                assert first == supervision["first_submission_calls"]
                stats = checkpoint["generation_model_stats"]
            else:
                first = expected_all
                stats = result["model_stats"]
            legacy.update(input=stats["tokens_sent"], output=stats["tokens_received"])
            coverage["generation_attempts_expected"] += first
            proc = read(stage / "process.json")
            # Submission cleanup may replay the original author trajectory under
            # a new PID without issuing generation calls. Follow its frozen
            # checkpoint rather than claiming those original calls are missing.
            if (stage / "submission-recovery.json").exists():
                recovery = read(stage / "submission-recovery.json")
                checkpoint_dir = Path(recovery["source"])
                assert recovery["original_model_calls"] == first
                proc = read(checkpoint_dir / "process.json")
                result_for_window = read(checkpoint_dir / "result.json")
                expected_all = recovery["original_model_calls"]
                coverage["recovered_generation_stages"] += 1
            else:
                result_for_window = result
            start = datetime.fromtimestamp(proc["started_at"] - 2, timezone.utc).isoformat()
            end = datetime.fromtimestamp(result_for_window["finished_at"] + 2, timezone.utc).isoformat()
            rows = list(ledger.execute(
                "SELECT * FROM requests WHERE pid=? AND started_at>=? AND started_at<=? ORDER BY started_at,id",
                (proc["pid"], start, end)))
            # A missing whole process is reportable; an unexplained partial match
            # is ambiguous and must not silently shift the P0 boundary.
            if len(rows) != expected_all:
                raise ValueError(f"Ambiguous ledger/process match: {model}/{job['stage']}")
            selected = rows[:first]  # Slice attempts BEFORE excluding failed/unknown usage.
            coverage["generation_attempts_matched"] += len(selected)
            coverage["generation_attempts_missing"] += first - len(selected)
            if len(selected) != first:
                incomplete_tasks.add(job["instance_id"])
            for row in selected:
                assert row["model"].removeprefix("openai/") == model
                assert row["task_id"] == job["stage"]
                if not row["usage_available"]:
                    coverage["generation_attempts_unknown_usage"] += 1
                    continue
                coverage["generation_requests_with_usage"] += 1
            totals.ledger_rows(selected, coverage)
        expected_legacy = sum(d["usage"]["P0_generation_tokens"] + d["usage"].get("upstream_candidate_summary_tokens", 0)
                              for d in comparison["details"] if d["model"] == model)
        expected_legacy += sum(read(locations / model / t["instance_id"] / "report.json")["usage"]["total_tokens"] for t in tasks)
        assert legacy["input"] + legacy["output"] == expected_legacy
        coverage["generation_tasks_mapped"] = 51 - len(incomplete_tasks)
        results[model] = {
            "model": model, "experiment": "P0", "tasks": 51,
            "passed": comparison["models"][model]["P0_passed"],
            "legacy_input_tokens": legacy["input"], "legacy_output_tokens": legacy["output"],
            "provider_usage": totals.result(), "provider_coverage": dict(coverage),
            "all_tasks_matched": not incomplete_tasks,
            "all_success_usage_available": not totals.counts["cache_unknown_requests"],
            "full_provider_usage_available": not incomplete_tasks and not totals.counts["cache_unknown_requests"] and not coverage["failed_requests_without_usage"],
            "missing_summary_usage": False,
        }
    return results


def stamp(value):
    return datetime.fromisoformat(value).timestamp()


def select_generation(ledger, model, task, original_tasks):
    if task.get("generation_reused") and "started_at" not in task:
        task = next(t for t in original_tasks if t["instance_id"] == task["instance_id"])
    start = min(task[k] for k in ("started_at", "agent_process_started_at", "original_agent_process_started_at") if task.get(k))
    parameters = (task["instance_id"], "openai/" + model, start, task["finished_at"])
    candidates = list(ledger.execute(
        "SELECT * FROM requests WHERE task_id=? AND model=? AND stage='generate' AND started_at>=? AND started_at<=? ORDER BY started_at,id",
        parameters))
    if "last_agent_pid" in task:
        rows = [r for r in candidates if r["pid"] == task["last_agent_pid"]]
    else:
        # Early retained pilot records have no PID. Require a unique process
        # matching the task, model, run time window AND saved call count.
        groups = {}
        for r in candidates:
            groups.setdefault(r["pid"], []).append(r)
        matches = [v for v in groups.values() if len(v) == task["model_stats"]["api_calls"]]
        if len(matches) != 1:
            raise ValueError("Ambiguous early-run PID")
        rows = matches[0]
    # Transport/parse retries may be billed without incrementing model_stats.
    if len(rows) < task["model_stats"]["api_calls"]:
        raise ValueError("Ledger does not cover all retained generation calls")
    return rows


def select_summaries(ledger, model, files, backup):
    summaries = {k.split("/")[0]: v for k, v in files.items() if k.endswith("/summary_result.json")}
    assert len(summaries) == 51 and all(v["attempts"] == 1 for v in summaries.values())
    day = min(v["finished_at"] for v in summaries.values())[:10]
    last = max(v["finished_at"] for v in summaries.values())
    rows = list(ledger.execute(
        "SELECT * FROM requests WHERE stage='patch_comparison' AND model=? AND started_at>=? AND started_at<=? ORDER BY started_at,id",
        (model, day + "T00:00:00+00:00", last)))
    if model == "qwen3.7-plus":
        # One dedicated summary worker issued exactly these 51 requests.
        assert len(rows) == 51 and len({r["pid"] for r in rows}) == 1
    else:
        events = [json.loads(line) for line in (backup / "analysis/hard51_ds_glm_tts30_20261104/events.jsonl").read_text().splitlines()
                  if "summary开始" in line]
        cohorts = {}
        for iid, summary in summaries.items():
            applicable = [e for e in events if e["instance_id"] == iid and stamp(e["time"]) <= stamp(summary["finished_at"])]
            event = max(applicable, key=lambda e: stamp(e["time"]))
            cohorts.setdefault(stamp(event["time"]), []).append(stamp(summary["finished_at"]))
        selected = []
        for begin, finishes in cohorts.items():
            # Events are stored to the second; allow logging/setup delay <2s.
            cohort = [r for r in rows if 0 <= stamp(r["started_at"]) - begin < 2
                      and r["finished_at"] and stamp(r["finished_at"]) <= max(finishes)]
            assert len(cohort) == len(finishes), "Summary cohort is ambiguous"
            selected.extend(cohort)
        rows = selected
    assert len(rows) == len({r["id"] for r in rows}) == 51
    assert all(r["usage_available"] for r in rows)
    return rows


def recount(workspace, remote_ledger, remote_metadata):
    backup = workspace / "remote-backups/jiangty9-156-15-20261106/TSE-Revision"
    runs = backup / "PhoenixRepair/validation_runs"
    scores = read(backup / "analysis/glm52_qwen37_environment_audit_20261105/sample123_scores_after.json")["scores"]
    final_scores = read(backup / "analysis/glm52_qwen37_environment_audit_20261105/final_score_audit.json")["summary"]
    with tempfile.TemporaryDirectory() as tmp:
        source = sqlite3.connect((workspace / "api_usage/usage.sqlite3").as_uri() + "?mode=ro", uri=True)
        ledger = sqlite3.connect(str(Path(tmp) / "snapshot.sqlite3"))
        source.backup(ledger)
        source.close()
        ledger.row_factory = sqlite3.Row
        p0 = p0_rows(workspace / "PhoenixRepair-strategy/experiments", ledger)
        ledger.close()
    remote = sqlite3.connect(remote_ledger.as_uri() + "?mode=ro", uri=True)
    remote.row_factory = sqlite3.Row
    metadata = read(remote_metadata)
    original_tasks = metadata["pro-v2-hard51-qwen3.7-plus-thinking-ab6-off-20261104"]["progress.json"]["tasks"]
    rows = []
    for model, names in RUNS.items():
        cumulative = Counter()
        pipeline = RawTotals(model)
        pipeline_coverage = Counter()
        for index, name in enumerate(names):
            tasks = metadata[name]["progress.json"]["tasks"]
            assert len(tasks) == len({t["instance_id"] for t in tasks}) == 51
            counters = Counter()
            raw = RawTotals(model)
            coverage = Counter()
            for task in tasks:
                stats = task.get("model_stats")
                if stats is None:
                    stats = read(runs / name / task["instance_id"] / "baseline_report.json")["model_stats"]
                counters.update(input=stats["tokens_sent"], output=stats["tokens_received"])
                # Reused Qwen tasks store their full stats in baseline_report.
                enriched = dict(task, model_stats=stats)
                matches = select_generation(remote, model, enriched, original_tasks)
                raw.ledger_rows(matches, coverage)
                coverage["generation_tasks_mapped"] += 1
            cumulative.update(counters)
            pipeline.merge(raw)
            pipeline_coverage.update(coverage)
            if index < 3:
                label = f"{'GLM 5.2' if model == 'glm-5.2' else 'Qwen 3.7'} sample {index + 1}"
                passed = next(s["resolved"] for s in scores if s["label"] == label)
                used = counters
                api, api_coverage = raw, coverage
            else:
                prefix = "glm" if model == "glm-5.2" else "qwen"
                passed = next(s["resolved"] for k, s in final_scores.items() if k.startswith(prefix))
                used = cumulative
                summary_rows = select_summaries(remote, model, metadata[name], backup)
                pipeline.ledger_rows(summary_rows, pipeline_coverage)
                pipeline_coverage["summary_tasks_mapped"] = 51
                api, api_coverage = pipeline, pipeline_coverage
            rows.append({
                "model": model, "experiment": f"Sample {index + 1}" if index < 3 else "TTS Final",
                "tasks": 51, "passed": passed,
                "legacy_input_tokens": used["input"], "legacy_output_tokens": used["output"],
                "provider_usage": api.result(), "provider_coverage": dict(api_coverage),
                "all_tasks_matched": True,
                "all_success_usage_available": not api.counts["cache_unknown_requests"],
                "full_provider_usage_available": not api.counts["cache_unknown_requests"] and not api_coverage["failed_requests_without_usage"], "missing_summary_usage": False,
            })
        rows.append(p0[model])
    remote.close()
    for row in rows:
        row["legacy_input_per_task"] = row["legacy_input_tokens"] / 51
        row["legacy_output_per_task"] = row["legacy_output_tokens"] / 51
        row["legacy_total_per_task"] = row["legacy_input_per_task"] + row["legacy_output_per_task"]
        row["full_billed_cost_usd_per_task"] = None
        usage = row["provider_usage"]
        for key in ("input_tokens", "output_tokens", "cached_input_tokens", "total_tokens"):
            row[key + "_per_task"] = usage[key] / 51
        row["uncached_input_tokens_per_task"] = (usage["input_tokens"] - usage["cached_input_tokens"]) / 51
        row["list_cost_cny_per_task"] = str(Decimal(usage["known_usage_list_cost_cny"]) / 51)
        row["list_cost_usd_per_task"] = str(Decimal(usage["known_usage_list_cost_usd"]) / 51)
        row["requests_without_usage"] = row["provider_coverage"].get("failed_requests_without_usage", 0)
        assert row["all_tasks_matched"] and row["all_success_usage_available"]
    return {
        "version": 2, "price_checked_on": "2026-11-08", "fx_date": "2026-11-06",
        "cny_per_usd": str(FX), "region": "China mainland / Beijing", "billing": "public PAYG list prices; implicit cache",
        "rates_cny_per_million": {m: dict(zip(("uncached_input", "cached_input", "output"), map(str, rates))) for m, rates in RATES.items()},
        "sources": {
            "glm": "https://help.aliyun.com/zh/model-studio/glm-5-2",
            "qwen": "https://help.aliyun.com/zh/model-studio/qwen3-7-plus",
            "fx": "https://www.chinamoney.com.cn/chinese/bkccpr/",
        },
        "private_input_sha256": {
            "remote_ledger": hashlib.sha256(remote_ledger.read_bytes()).hexdigest(),
            "remote_run_metadata": hashlib.sha256(remote_metadata.read_bytes()).hexdigest(),
        },
        "notes": [
            "Legacy model_stats are retained for comparison, not provider billing counts; output may omit tool-call arguments.",
            "Cached tokens are a subset of input, never an additional token total.",
            "All retained generation tasks and summaries are matched. Failed HTTP requests without usage are counted separately and not assigned invented token charges.",
            "full_provider_usage_available remains false when errors/timeouts/pending requests have no returned usage; all_success_usage_available refers only to matched responses that returned usage.",
            "P0 counts reused localization, candidates, summary and the final author up to first submission; excludes review and repair.",
            "TTS includes all three samples, summary and final, including billed transport/parse retries in retained processes.",
            "No account IDs, request IDs, task IDs, private paths, prompts, patches or credentials are exported.",
            "Discounts, free quota, explicit-cache creation/storage and historical superseded runs are not billed here. This is not an invoice.",
        ],
        "rows": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--remote-ledger", required=True, type=Path)
    parser.add_argument("--remote-metadata", required=True, type=Path)
    args = parser.parse_args()
    report = recount(args.workspace, args.remote_ledger, args.remote_metadata)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    csv_path = args.output.with_suffix(".csv")
    fields = ["model", "experiment", "tasks", "passed", "input_tokens_per_task", "uncached_input_tokens_per_task", "cached_input_tokens_per_task", "output_tokens_per_task", "total_tokens_per_task", "list_cost_cny_per_task", "list_cost_usd_per_task", "legacy_input_per_task", "legacy_output_per_task",
              "legacy_total_per_task", "full_billed_cost_usd_per_task",
              "all_tasks_matched", "all_success_usage_available", "full_provider_usage_available", "requests_without_usage", "missing_summary_usage"]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(report["rows"])
    print(f"Wrote {len(report['rows'])} aggregate rows to {args.output} and {csv_path}")


if __name__ == "__main__":
    main()
