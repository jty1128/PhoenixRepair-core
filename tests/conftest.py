import copy
import os

import pytest

# No model metadata downloads are needed by offline tests.
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"


@pytest.fixture
def task():
    return dict(instance_id="toy-task", problem_statement="Return 2 instead of 1.",
                base_commit="abc123", repo_name="fixture", image_name="fixture:local")


@pytest.fixture
def candidate():
    areas = ("entrypoints", "state_and_defaults", "consumers_and_contracts",
             "persistence_and_serialization", "dependencies_and_registration", "compatibility_and_errors")
    return dict(id="C1", hypothesis="Change the returned value.",
        planning_mode="complete_strategy_v1", schema_version="location_checks_v1",
        requirements=[dict(id="R1", text="Return 2 instead of 1.")],
        implementation_outline="Change value at the single entry point; there is no stored state.",
        files_to_modify=[dict(file_path="app.py", modifications=[dict(id="L1",
            location=dict(start_line=1, end_line=1), current_code="value = 1",
            reason="Set the required value to 2.")])],
        requirement_coverage=[dict(requirement_id="R1", status="change", location_ids=["L1"],
                                   explanation="The entry point returns the requested value.")],
        dependency_review=[dict(area=area, status="not_applicable", location_ids=[],
                                explanation="This fixture contains only a constant.") for area in areas])


@pytest.fixture
def settings(tmp_path):
    from phoenix.settings import ROOT, load_settings
    result = load_settings("our_method", ROOT / "configs/our_method.yaml", {})
    result.update(output=str(tmp_path / "output"), runtime=str(tmp_path / "runtime"))
    return result
