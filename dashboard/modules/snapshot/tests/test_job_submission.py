import logging
import os
import sys
import time
import json
import jsonschema

import pprint
import pytest
import requests

import ray
from ray._private.test_utils import (
    format_web_url,
    wait_for_condition,
    wait_until_server_available,
)
from ray.dashboard import dashboard
from ray.dashboard.tests.conftest import *  # noqa
from ray.job_submission import JobSubmissionClient

logger = logging.getLogger(__name__)


def _get_snapshot(address: str):
    response = requests.get(f"{address}/api/snapshot")
    response.raise_for_status()
    data = response.json()
    schema_path = os.path.join(
        os.path.dirname(dashboard.__file__), "modules/snapshot/snapshot_schema.json"
    )
    pprint.pprint(data)
    jsonschema.validate(instance=data, schema=json.load(open(schema_path)))
    return data


@pytest.mark.parametrize("address_suffix", ["", "/"])  # Trailing slash should succeed
@pytest.mark.parametrize(
    "set_override_dashboard_url",
    [
        None,
        "https://external_dashboard_url",
        "https://external_dashboard_url/path1/?query_param1=val1&query_param2=val2",
        "new_external_dashboard_url",
    ],
    indirect=True,
)
def test_successful_job_status(
    make_sure_dashboard_http_port_unused,
    set_override_dashboard_url,
    ray_start_with_dashboard,
    disable_aiohttp_cache,
    enable_test_module,
    address_suffix,
):
    address = ray._private.worker._global_node.webui_url
    assert wait_until_server_available(address)
    address = format_web_url(address)

    job_sleep_time_s = 5
    entrypoint = (
        'python -c"'
        "import ray;"
        "ray.init();"
        "import time;"
        f"time.sleep({job_sleep_time_s});"
        '"'
    )

    client = JobSubmissionClient(address + address_suffix)
    start_time_s = int(time.time())
    runtime_env = {"env_vars": {"RAY_TEST_123": "123"}}
    metadata = {"ray_test_456": "456"}
    job_id = client.submit_job(
        entrypoint=entrypoint, metadata=metadata, runtime_env=runtime_env
    )

    def wait_for_job_to_succeed():
        data = _get_snapshot(address)
        legacy_job_succeeded = False
        job_succeeded = False

        # Test legacy job snapshot (one driver per job).
        for job_entry in data["data"]["snapshot"]["jobs"].values():
            if job_entry["status"] is not None:
                assert job_entry["config"]["metadata"]["jobSubmissionId"] == job_id
                assert job_entry["status"] in {"PENDING", "RUNNING", "SUCCEEDED"}
                assert job_entry["statusMessage"] is not None
                legacy_job_succeeded = job_entry["status"] == "SUCCEEDED"

        # Test new jobs snapshot (0 to N drivers per job).
        assert data["data"]["snapshot"]["jobSubmission"]
        for job_submission_id, entry in data["data"]["snapshot"][
            "jobSubmission"
        ].items():
            if entry["status"] is not None:
                assert entry["jobSubmissionId"] == job_id
                assert entry["entrypoint"] == entrypoint
                assert entry["status"] in {"PENDING", "RUNNING", "SUCCEEDED"}
                assert entry["message"] is not None
                # TODO(architkulkarni): Disable automatic camelcase.
                assert entry["runtimeEnv"] == {"envVars": {"RAYTest123": "123"}}
                assert entry["metadata"] == {"rayTest456": "456"}
                assert entry["errorType"] is None
                assert abs(entry["startTime"] - start_time_s * 1000) <= 10000
                if entry["status"] == "SUCCEEDED":
                    job_succeeded = True
                    assert (
                        entry["endTime"] >= entry["startTime"] + job_sleep_time_s * 1000
                    )

        print(f"Legacy job submission succeeded: {legacy_job_succeeded}")
        print(f"Job submission succeeded: {job_succeeded}")
        return legacy_job_succeeded and job_succeeded

    wait_for_condition(wait_for_job_to_succeed, timeout=45)


@pytest.mark.parametrize("address_suffix", ["", "/"])  # Trailing slash should succeed
def test_failed_job_status(
    make_sure_dashboard_http_port_unused,
    ray_start_with_dashboard,
    disable_aiohttp_cache,
    enable_test_module,
    address_suffix,
):
    address = ray._private.worker._global_node.webui_url
    assert wait_until_server_available(address)
    address = format_web_url(address)

    job_sleep_time_s = 5
    entrypoint = (
        'python -c"'
        "import ray;"
        "ray.init();"
        "import time;"
        f"time.sleep({job_sleep_time_s});"
        "import sys;"
        "sys.exit(1);"
        '"'
    )
    start_time_s = int(time.time())
    client = JobSubmissionClient(address + address_suffix)
    runtime_env = {"env_vars": {"RAY_TEST_456": "456"}}
    metadata = {"ray_test_789": "789"}
    job_id = client.submit_job(
        entrypoint=entrypoint, metadata=metadata, runtime_env=runtime_env
    )

    def wait_for_job_to_fail():
        data = _get_snapshot(address)

        legacy_job_failed = False
        job_failed = False

        # Test legacy job snapshot (one driver per job).
        for job_entry in data["data"]["snapshot"]["jobs"].values():
            if job_entry["status"] is not None:
                assert job_entry["config"]["metadata"]["jobSubmissionId"] == job_id
                assert job_entry["status"] in {"PENDING", "RUNNING", "FAILED"}
                assert job_entry["statusMessage"] is not None
                legacy_job_failed = job_entry["status"] == "FAILED"

        # Test new jobs snapshot (0 to N drivers per job).
        assert data["data"]["snapshot"]["jobSubmission"]
        for job_submission_id, entry in data["data"]["snapshot"][
            "jobSubmission"
        ].items():
            if entry["status"] is not None:
                assert entry["jobSubmissionId"] == job_id
                assert entry["entrypoint"] == entrypoint
                assert entry["status"] in {"PENDING", "RUNNING", "FAILED"}
                assert entry["message"] is not None
                # TODO(architkulkarni): Disable automatic camelcase.
                assert entry["runtimeEnv"] == {"envVars": {"RAYTest456": "456"}}
                assert entry["metadata"] == {"rayTest789": "789"}
                assert entry["errorType"] is None
                assert abs(entry["startTime"] - start_time_s * 1000) <= 10000
                if entry["status"] == "FAILED":
                    job_failed = True
                    assert (
                        entry["endTime"] >= entry["startTime"] + job_sleep_time_s * 1000
                    )

        print(f"Legacy job submission failed: {legacy_job_failed}")
        print(f"Job submission failed: {job_failed}")
        return legacy_job_failed and job_failed

    wait_for_condition(wait_for_job_to_fail, timeout=45)


if __name__ == "__main__":
    sys.exit(pytest.main(["-v", __file__]))
