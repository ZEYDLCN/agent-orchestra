import fakeredis
import pytest
from fastapi.testclient import TestClient

import orchestrator.main as main_module
from orchestrator.config import settings
from orchestrator.models import AgentRun, AgentRunRequest, LLMTrace, WorkerInfo


@pytest.fixture
def client(monkeypatch):
    server = fakeredis.FakeServer()
    # rebind_client (not a bare .client = ...) also re-registers the Lua
    # scripts against the fake server -- redis-py binds a Script to
    # whichever client registered it, so a plain attribute swap would
    # leave ack/nack/cancel/claim silently talking to whatever real Redis
    # task_queue was originally constructed with.
    main_module.task_queue.rebind_client(fakeredis.FakeStrictRedis(server=server, decode_responses=True))
    main_module.message_bus.client = fakeredis.FakeStrictRedis(server=server, decode_responses=True)
    main_module.agent_workflow.rebind_client(
        fakeredis.FakeStrictRedis(server=server, decode_responses=True)
    )
    main_module.worker_manager.registry.client = fakeredis.FakeStrictRedis(
        server=server, decode_responses=True
    )
    # rate limiter shares the same Redis; keep it out of the way of tests
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)
    monkeypatch.setattr(settings, "api_key", None)
    return TestClient(main_module.app)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200


def test_start_agent_run_dispatches_goal(client, monkeypatch):
    workers = [
        WorkerInfo(worker_id=f"worker-{index}", worktree_path="workspace/test", branch="test")
        for index in range(3)
    ]
    captured = {}

    def start(request: AgentRunRequest):
        captured["request"] = request
        return AgentRun(goal=request.goal, max_rounds=request.max_rounds, trader_count=request.trader_count)

    monkeypatch.setattr(main_module.worker_manager, "list_workers", lambda: workers)
    monkeypatch.setattr(main_module.agent_workflow, "start", start)

    response = client.post(
        "/agent-runs",
        json={"goal": "En iyi stratejiyi bul", "max_rounds": 2, "max_tasks_per_round": 4, "trader_count": 3},
    )

    assert response.status_code == 200
    assert response.json()["stage"] == "planner"
    assert captured["request"].max_tasks_per_round == 4


def test_get_unknown_agent_run_404s(client):
    assert client.get("/agent-runs/does-not-exist").status_code == 404


def test_trace_list_and_summary_endpoints(client):
    main_module.agent_workflow.trace_store.record(
        LLMTrace(
            run_id="run-trace",
            role="planner",
            agent_id="planner",
            provider="mock",
            input_tokens=12,
            output_tokens=8,
            total_tokens=20,
            duration_ms=15,
            cost_usd=0,
        )
    )

    traces = client.get("/traces", params={"run_id": "run-trace"})
    summary = client.get("/traces/summary", params={"run_id": "run-trace"})

    assert traces.status_code == 200
    assert traces.json()[0]["agent_id"] == "planner"
    assert summary.status_code == 200
    assert summary.json()["total_tokens"] == 20


def test_ready_reports_component_checks(client, monkeypatch):
    monkeypatch.setattr(settings, "sandbox_enabled", False)  # skip real docker check
    resp = client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ready"] is True
    assert body["checks"] == {"redis": True, "docker_sandbox": True, "llm": True}


def test_ready_503s_when_llm_misconfigured_in_production(client, monkeypatch):
    from orchestrator.llm import factory

    monkeypatch.setattr(settings, "sandbox_enabled", False)
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    factory.reset_cache()

    resp = client.get("/ready")

    assert resp.status_code == 503
    assert resp.json()["ready"] is False
    assert resp.json()["checks"]["llm"] is False
    factory.reset_cache()


def test_ready_checks_ollama_used_by_agent_profile(client, monkeypatch):
    import orchestrator.llm.ollama_provider as ollama_module
    from orchestrator.llm import factory

    monkeypatch.setattr(settings, "sandbox_enabled", False)
    monkeypatch.setattr(settings, "llm_provider", "mock")
    monkeypatch.setattr(settings, "agent_profiles_raw", "qwen|ollama|qwen2.5:3b;reviewer|mock")
    monkeypatch.setattr(ollama_module, "is_ollama_available", lambda url, timeout=3.0: False)
    factory.reset_cache()

    resp = client.get("/ready")

    assert resp.status_code == 503
    assert resp.json()["checks"]["llm"] is False
    assert "not reachable" in resp.json()["llm_error"]
    factory.reset_cache()


def test_submit_and_get_job(client):
    resp = client.post("/jobs", json={"tasks": [{"params": {"fast_ma": 5, "slow_ma": 50}}]})
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    job = client.get(f"/jobs/{job_id}").json()
    assert job["total"] == 1
    assert job["pending_or_running"] == 1


def test_submit_job_rejects_empty_task_list(client):
    resp = client.post("/jobs", json={"tasks": []})
    assert resp.status_code == 422  # pydantic min_length=1


def test_get_unknown_job_404s(client):
    resp = client.get("/jobs/does-not-exist")
    assert resp.status_code == 404


def test_list_jobs_includes_submitted_job(client):
    job_id = client.post("/jobs", json={"tasks": [{"params": {}}]}).json()["job_id"]

    jobs = client.get("/jobs?limit=5").json()

    assert any(j["job_id"] == job_id for j in jobs)


def test_cancel_pending_job(client):
    job_id = client.post("/jobs", json={"tasks": [{"params": {}}, {"params": {}}]}).json()["job_id"]

    resp = client.post(f"/jobs/{job_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["cancelled"] == 2

    job = client.get(f"/jobs/{job_id}").json()
    assert job["cancelled"] == 2
    assert job["pending_or_running"] == 0


def test_cancel_unknown_job_404s(client):
    assert client.post("/jobs/does-not-exist/cancel").status_code == 404


def test_retry_task_requires_failed_or_cancelled_status(client):
    job_id = client.post("/jobs", json={"tasks": [{"params": {}}]}).json()["job_id"]
    task_id = client.get(f"/jobs/{job_id}").json()["tasks"][0]["task_id"]

    # still pending, not eligible for retry yet
    resp = client.post(f"/jobs/{job_id}/tasks/{task_id}/retry")
    assert resp.status_code == 409


def test_retry_unknown_task_404s(client):
    job_id = client.post("/jobs", json={"tasks": [{"params": {}}]}).json()["job_id"]
    resp = client.post(f"/jobs/{job_id}/tasks/does-not-exist/retry")
    assert resp.status_code == 404


def test_delete_job_removes_it(client):
    job_id = client.post("/jobs", json={"tasks": [{"params": {}}]}).json()["job_id"]

    resp = client.delete(f"/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["tasks_removed"] == 1

    assert client.get(f"/jobs/{job_id}").status_code == 404


def test_refine_job_requires_completed_tasks(client):
    job_id = client.post("/jobs", json={"tasks": [{"params": {"fast_ma": 5, "slow_ma": 50}}]}).json()["job_id"]

    # tasks are still pending (no worker in this test), refine should refuse
    resp = client.post(f"/jobs/{job_id}/refine")
    assert resp.status_code == 409


def test_refine_job_builds_narrower_sweep_from_results(client):
    job_id = client.post("/jobs", json={"tasks": [{"params": {"fast_ma": 5, "slow_ma": 50}}]}).json()["job_id"]
    task_id = client.get(f"/jobs/{job_id}").json()["tasks"][0]["task_id"]
    main_module.task_queue.pop_task(["backtest"], "worker-1", timeout=1)
    main_module.task_queue.ack(task_id, {"score": 90.0, "params": {"fast_ma": 5, "slow_ma": 50}})

    resp = client.post(f"/jobs/{job_id}/refine")

    assert resp.status_code == 200
    new_job_id = resp.json()["job_id"]
    assert len(resp.json()["task_ids"]) > 0
    new_job_meta = client.get(f"/jobs/{new_job_id}").json()["meta"]
    assert new_job_meta["parent_job_id"] == job_id
    assert new_job_meta["kind"] == "refine"


def test_events_stream_404s_for_unknown_job(client):
    resp = client.get("/jobs/does-not-exist/events")
    assert resp.status_code == 404


def test_api_key_gates_mutating_endpoints(client, monkeypatch):
    monkeypatch.setattr(settings, "api_key", "secret123")

    resp = client.post("/jobs", json={"tasks": [{"params": {}}]})
    assert resp.status_code == 401

    resp = client.post(
        "/jobs", json={"tasks": [{"params": {}}]}, headers={"X-API-Key": "secret123"}
    )
    assert resp.status_code == 200


def test_api_key_does_not_gate_read_endpoints(client, monkeypatch):
    monkeypatch.setattr(settings, "api_key", "secret123")
    assert client.get("/workers").status_code == 200
    assert client.get("/health").status_code == 200
