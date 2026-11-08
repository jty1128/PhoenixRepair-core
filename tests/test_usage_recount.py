"""Billing audit invariants using synthetic records, without credentials/API calls."""
import importlib.util
import sqlite3
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "usage_recount", Path(__file__).parents[1] / "scripts/recount_hard51_usage.py"
)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def test_cached_tokens_replace_normal_input_instead_of_being_added():
    assert audit.cost_cny("glm-5.2", 1_000_000, 50_000, 750_000) == Decimal("4.9")
    assert audit.cost_cny("qwen3.7-plus", 100_000, 1_000, 80_000) == Decimal("0.08")


def test_unknown_cache_is_not_zero_cache():
    assert audit.cost_cny("glm-5.2", 100, 10, None) is None
    total = audit.RawTotals("glm-5.2")
    total.add("synthetic", 100, 10, None)
    assert total.result()["cache_unknown_requests"] == 1


def test_reused_stage_cannot_be_billed_twice_in_one_pipeline():
    a, b = audit.RawTotals("glm-5.2"), audit.RawTotals("glm-5.2")
    a.add("same-request", 100, 10, 50)
    b.add("same-request", 100, 10, 50)
    with pytest.raises(ValueError, match="more than one"):
        a.merge(b)


def test_failed_request_keeps_its_position_at_p0_boundary():
    # P0 made two attempts, one failed. Repair is the third request and must
    # not be shifted into P0 merely because the second has no usage.
    rows = [
        dict(id="author", status="success", http_status=200, usage_available=1,
             input_tokens=100, output_tokens=10, total_tokens=110, cached_input_tokens=50),
        dict(id="error", status="error", http_status=500, usage_available=0),
        dict(id="repair", status="success", http_status=200, usage_available=1,
             input_tokens=999, output_tokens=99, total_tokens=1098, cached_input_tokens=0),
    ]
    total, coverage = audit.RawTotals("glm-5.2"), Counter()
    total.ledger_rows(rows[:2], coverage)
    assert total.result()["input_tokens"] == 100
    assert coverage["failed_requests_without_usage"] == 1
    assert coverage["request_attempts"] == 2


def test_success_without_usage_cannot_pass_as_complete():
    total = audit.RawTotals("glm-5.2")
    with pytest.raises(ValueError, match="no provider usage"):
        total.ledger_rows([dict(status="success", usage_available=0)], Counter())


def test_invalid_cache_and_unexpected_price_tier_are_rejected():
    with pytest.raises(ValueError):
        audit.cost_cny("glm-5.2", 100, 10, 101)
    with pytest.raises(ValueError, match="pricing tier"):
        audit.RawTotals("qwen3.7-plus").add("long-input", 300_000, 10, 0)


def test_resumed_task_includes_original_process_and_excludes_parallel_sample():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE requests(id TEXT, task_id TEXT, model TEXT, stage TEXT, pid INTEGER, started_at TEXT)")
    db.executemany("INSERT INTO requests VALUES(?,?,?,?,?,?)", [
        ("first", "toy", "openai/glm-5.2", "generate", 10, "2026-11-04T10:01:00+00:00"),
        ("resumed", "toy", "openai/glm-5.2", "generate", 10, "2026-11-04T10:20:00+00:00"),
        ("parallel-sample", "toy", "openai/glm-5.2", "generate", 20, "2026-11-04T10:02:00+00:00"),
    ])
    task = dict(instance_id="toy", last_agent_pid=10, started_at="2026-11-04T10:00:00+00:00",
                original_agent_process_started_at="2026-11-04T10:00:01+00:00",
                agent_process_started_at="2026-11-04T10:15:00+00:00",
                finished_at="2026-11-04T10:21:00+00:00", model_stats={"api_calls": 2})
    assert [r["id"] for r in audit.select_generation(db, "glm-5.2", task, [])] == ["first", "resumed"]
