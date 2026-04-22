"""
Integration test for the AgentCert API.

Submits a bucketing-extraction job using the local trace file at
  trace_dump/traces_april1.json
then polls until completion (or failure) and prints the result.

Usage:
    python test_api.py [--host localhost] [--port 8000]
"""

import argparse
import json
import sys
import time

import requests

TRACE_FILE = "/srv/projects/mas/mars/agent-cert/certifier/trace_dump/traces_april1.json"
POLL_INTERVAL_S = 10
TIMEOUT_S = 600  # 10 minutes

LANGFUSE_HOST = "http://localhost:3001"
LANGFUSE_PUBLIC_KEY = "pk-lf-58182f11-fd67-407f-b043-57bf9d0be1b5"
LANGFUSE_SECRET_KEY = "sk-lf-7f9db300-77d3-4b40-9a60-5687abf95aee"


def submit_job(base_url: str) -> str:
    payload = {
        "agent_id": "test-agent-001",
        "experiment_id": "exp_april1",
        "run_id": "run_001",
        "trace_source": {
            "type": "file",
            "file_path": TRACE_FILE,
        },
        "llm_batch_size": 10,
        "storage_config": {
            "type": "local",
        },
    }

    print(f"\n[POST] {base_url}/api/v1/bucketing-extraction")
    resp = requests.post(f"{base_url}/api/v1/bucketing-extraction", json=payload, timeout=30)

    if resp.status_code == 409:
        existing = resp.json()
        print(f"[409] Duplicate active task: {existing}")
        existing_task_id = existing.get("detail", {}).get("details", {}).get("task_id")
        if existing_task_id:
            print(f"  → Polling existing task: {existing_task_id}")
            return existing_task_id
        sys.exit(1)

    if resp.status_code != 202:
        print(f"[ERROR] {resp.status_code}: {resp.text}")
        sys.exit(1)

    body = resp.json()
    task_id = body["task_id"]
    print(f"[202] task_id: {task_id}")
    print(f"      poll_url: {body['poll_url']}")
    return task_id


def poll_until_done(base_url: str, task_id: str) -> dict:
    url = f"{base_url}/api/v1/tasks/{task_id}"
    deadline = time.monotonic() + TIMEOUT_S
    last_stage = None

    while time.monotonic() < deadline:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 404:
            print(f"[404] Task not found: {task_id}")
            sys.exit(1)

        task = resp.json()
        status = task.get("status")
        stage = task.get("stage")

        if stage != last_stage:
            elapsed = time.strftime("%H:%M:%S")
            print(f"[{elapsed}] status={status}  stage={stage}")
            last_stage = stage

        if status == "COMPLETED":
            return task
        if status == "FAILED":
            print(f"\n[FAILED] error={json.dumps(task.get('error'), indent=2)}")
            sys.exit(1)

        time.sleep(POLL_INTERVAL_S)

    print(f"[TIMEOUT] Task {task_id} did not complete within {TIMEOUT_S}s")
    sys.exit(1)


def print_result(task: dict) -> None:
    data = task.get("result") or task.get("data", {})
    print("\n" + "=" * 60)
    print("COMPLETED")
    print("=" * 60)
    print(f"  total_observations   : {data.get('total_observations')}")
    print(f"  total_faults_detected: {data.get('total_faults_detected')}")
    print(f"  processing_time_s    : {data.get('processing_time_seconds')}")

    faults = data.get("faults", [])
    print(f"\n  Faults ({len(faults)}):")
    for f in faults:
        print(f"    [{f['fault_id']}]  severity={f['severity']}  "
              f"status={f['status']}  "
              f"detected_at={f.get('detected_at')}")

    token_usage = data.get("token_usage", {})
    print(f"\n  Token usage:")
    print(f"    bucketing  : {token_usage.get('bucketing_input_tokens')} in / "
          f"{token_usage.get('bucketing_output_tokens')} out")
    print(f"    extraction : {token_usage.get('extraction_input_tokens')} in / "
          f"{token_usage.get('extraction_output_tokens')} out")
    print(f"    total      : {token_usage.get('total_tokens')}")

    paths = data.get("storage_paths", {})
    print(f"\n  Output:")
    print(f"    metrics_dir : {paths.get('metrics_dir')}")
    print(f"    summary     : {paths.get('summary')}")
    print("=" * 60)


def test_duplicate_rejection(base_url: str) -> None:
    """Submit the same job again — must get 409."""
    print(f"\n[TEST] Duplicate rejection")
    payload = {
        "agent_id": "test-agent-001",
        "experiment_id": "exp_april1",
        "run_id": "run_001",
        "trace_source": {"type": "file", "file_path": TRACE_FILE},
        "llm_batch_size": 10,
        "storage_config": {"type": "local"},
    }
    resp = requests.post(f"{base_url}/api/v1/bucketing-extraction", json=payload, timeout=10)
    if resp.status_code == 409:
        print("  → [PASS] 409 Conflict returned as expected")
    else:
        print(f"  → [FAIL] Expected 409, got {resp.status_code}: {resp.text}")


def test_missing_file(base_url: str) -> None:
    """Submit with a non-existent trace file — task should fail with TRACE_NOT_FOUND."""
    print(f"\n[TEST] Missing trace file")
    payload = {
        "agent_id": "test-agent-001",
        "experiment_id": "exp_missing",
        "run_id": "run_bad",
        "trace_source": {"type": "file", "file_path": "/nonexistent/trace.json"},
        "llm_batch_size": 5,
        "storage_config": {"type": "local"},
    }
    resp = requests.post(f"{base_url}/api/v1/bucketing-extraction", json=payload, timeout=10)
    if resp.status_code != 202:
        print(f"  → [FAIL] Expected 202, got {resp.status_code}: {resp.text}")
        return
    task_id = resp.json()["task_id"]
    time.sleep(3)  # Give background task a moment to fail
    task = requests.get(f"{base_url}/api/v1/tasks/{task_id}", timeout=10).json()
    if task.get("status") == "FAILED" and task.get("error", {}).get("error_code") == "TRACE_NOT_FOUND":
        print(f"  → [PASS] Task {task_id} failed with TRACE_NOT_FOUND")
    else:
        print(f"  → [FAIL] Unexpected state: {json.dumps(task, indent=2, default=str)}")


def test_task_not_found(base_url: str) -> None:
    """GET a non-existent task_id — must return 404."""
    print(f"\n[TEST] Task not found")
    resp = requests.get(f"{base_url}/api/v1/tasks/00000000-0000-0000-0000-000000000000", timeout=10)
    if resp.status_code == 404:
        print("  → [PASS] 404 returned as expected")
    else:
        print(f"  → [FAIL] Expected 404, got {resp.status_code}: {resp.text}")


def test_langfuse_empty_project(base_url: str) -> None:
    """
    Submit a Langfuse job against the configured project.
    If the project has no traces the task should fail with TRACE_NOT_FOUND.
    If it has traces it should transition to running_pipeline.
    """
    print(f"\n[TEST] Langfuse source — {LANGFUSE_HOST}")
    from datetime import datetime, timezone
    from_ts = "2026-01-01T00:00:00Z"  # wide window to catch all traces

    payload = {
        "agent_id": "test-agent-001",
        "experiment_id": "exp_langfuse_test",
        "run_id": "run_lf_001",
        "trace_source": {
            "type": "langfuse",
            "base_url": LANGFUSE_HOST,
            "public_key": LANGFUSE_PUBLIC_KEY,
            "secret_key": LANGFUSE_SECRET_KEY,
            "from_timestamp": from_ts,
            "page_size": 100,
            "max_pages": 5,
            "include_observations": True,
        },
        "llm_batch_size": 10,
        "storage_config": {"type": "local"},
    }
    resp = requests.post(f"{base_url}/api/v1/bucketing-extraction", json=payload, timeout=30)
    if resp.status_code != 202:
        print(f"  → [FAIL] Expected 202, got {resp.status_code}: {resp.text}")
        return
    task_id = resp.json()["task_id"]
    print(f"  task_id: {task_id}")

    # Poll up to 30s — if no traces it fails quickly; if traces exist it starts running
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        task = requests.get(f"{base_url}/api/v1/tasks/{task_id}", timeout=10).json()
        status, stage = task.get("status"), task.get("stage")
        if status == "FAILED":
            err = task.get("error", {})
            if err.get("error_code") == "TRACE_NOT_FOUND":
                print(f"  → [PASS] Project has no traces yet — TRACE_NOT_FOUND as expected")
            else:
                print(f"  → [INFO] Task failed: {err.get('error_code')} — {err.get('message')}")
            return
        if status == "RUNNING" and stage == "running_pipeline":
            print(f"  → [PASS] Langfuse fetch succeeded — pipeline is running (task will continue in background)")
            return
        if status == "COMPLETED":
            print(f"  → [PASS] Langfuse pipeline completed")
            print_result(task)
            return
        time.sleep(3)
    print(f"  → [INFO] Task still {status}/{stage} after 30s (pipeline running — check separately)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    base_url = f"http://{args.host}:{args.port}"

    # ── Quick smoke tests (no LLM calls) ──────────────────────────────────
    test_missing_file(base_url)
    test_task_not_found(base_url)
    test_langfuse_empty_project(base_url)

    # ── Main pipeline run ──────────────────────────────────────────────────
    print(f"\n[MAIN] Submitting pipeline job with traces_april1.json")
    task_id = submit_job(base_url)

    # ── Duplicate rejection (while job is running) ─────────────────────────
    time.sleep(1)
    test_duplicate_rejection(base_url)

    # ── Poll to completion ─────────────────────────────────────────────────
    task = poll_until_done(base_url, task_id)
    print_result(task)


if __name__ == "__main__":
    main()
