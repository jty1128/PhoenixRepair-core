"""Explicit historical model/method recovery profiles; never switch models."""

# Generation sampling parameters remain in settings.PROFILES. These values
# describe transport/lifecycle recovery, not another repair or review prompt.
MODEL_RECOVERY = {
    "glm-5.2": {
        "api_timeout_seconds": 240, "wall_time_seconds": 7200,
        "thinking": False, "summary_timeout_seconds": 120,
        "summary_transport_attempts": 5, "summary_format_rounds": 3,
        "tts_capture_reserve_seconds": 60, "p0_supported": True,
    },
    "qwen3.7-plus": {
        "api_timeout_seconds": 600, "wall_time_seconds": 7200,
        "thinking": False, "summary_timeout_seconds": 120,
        "summary_transport_attempts": 5, "summary_format_rounds": 3,
        "tts_capture_reserve_seconds": 60, "p0_supported": True,
    },
    "kimi-k2.7-code": {
        "api_timeout_seconds": 240, "wall_time_seconds": 7200,
        "thinking": True, "preserve_thinking": True,
        "summary_timeout_seconds": 240, "summary_transport_attempts": 3,
        "summary_format_rounds": 3, "tts_capture_reserve_seconds": 0,
        "p0_supported": False,
    },
}
MODEL_RECOVERY["deepseek-v4.1-flash"] = dict(MODEL_RECOVERY["glm-5.2"])


def recovery_profile(settings):
    model = settings["model"]
    result = dict(MODEL_RECOVERY[model], model=model, method=settings["method"],
                  same_model_only=True, budget_reset=False, review_calls=0,
                  environment_start_retries=3 if settings["method"] == "our_method" else 1,
                  terminal_session_retries=1, submission_replay_attempts=2)
    if settings["method"] == "our_method":
        if not result["p0_supported"]:
            raise ValueError("No historically scored Kimi P0 profile is available")
        result.update(summary_timeout_seconds=(30, result["api_timeout_seconds"]),
                      summary_transport_attempts=1, summary_format_rounds=1,
                      summary_request_interval_seconds=2,
                      author_budget="shared task API-call budget; no calls reserved for post-P0 review")
        result["p0_reserved_calls"] = settings.get("p0_reserved_calls", 0)
        if result["p0_reserved_calls"]:
            result["author_budget"] = "shared task API-call budget minus reserved calls; stop at first P0 submission"
    else:
        result["author_budget"] = "independent trajectory step cap; preserve already spent steps on recovery"
    return result
