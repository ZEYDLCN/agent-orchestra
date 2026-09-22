import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client.core import REGISTRY, CounterMetricFamily, GaugeMetricFamily, SummaryMetricFamily
from prometheus_fastapi_instrumentator import Instrumentator

from orchestrator.agent_profiles import parse_agent_profiles
from orchestrator.auth import require_api_key
from orchestrator.config import settings
from orchestrator.coordinator import propose_refinement
from orchestrator.llm.factory import build_role_provider, get_llm_provider
from orchestrator.logging_setup import configure_logging
from orchestrator.messaging import MessageBus
from orchestrator.models import (
    AgentRun,
    AgentRunRequest,
    JobSubmitRequest,
    JobSubmitResponse,
    ScaleRequest,
    Task,
    TaskStatus,
    WorkerInfo,
)
from orchestrator.queue import TaskQueue
from orchestrator.rate_limit import RateLimitMiddleware
from orchestrator.reaper import Reaper
from orchestrator.worker_manager import WorkerManager
from orchestrator.workflow import AgentWorkflow, PlannerAgent, ReviewerAgent

configure_logging()

task_queue = TaskQueue(settings.redis_url)
message_bus = MessageBus(settings.redis_url)
worker_manager = WorkerManager()
reaper = Reaper(task_queue)
control_provider = settings.control_llm_provider or settings.llm_provider
agent_workflow = AgentWorkflow(
    task_queue,
    message_bus,
    PlannerAgent(build_role_provider(control_provider, settings.control_llm_model)),
    ReviewerAgent(build_role_provider(control_provider, settings.control_llm_model)),
    settings.redis_url,
)

DASHBOARD_HTML_PATH = Path(__file__).resolve().parent / "static" / "dashboard.html"


class WorkerFleetCollector:
    """Reports the live worker count at scrape time (not a periodically
    updated gauge), so it never goes stale between scrapes."""

    def collect(self):
        gauge = GaugeMetricFamily(
            "orchestrator_workers_active", "Workers currently visible in the registry", labels=["status"]
        )
        # prometheus_client calls collect() eagerly at REGISTRY.register()
        # time (to validate metric names) and again on every /metrics
        # scrape -- neither should ever 500 just because Redis hiccuped.
        try:
            counts: dict[str, int] = {}
            for worker in worker_manager.list_workers():
                counts[worker.status] = counts.get(worker.status, 0) + 1
            for status, count in counts.items():
                gauge.add_metric([status], count)
        except Exception:  # noqa: BLE001
            pass
        yield gauge


class TaskMetricsCollector:
    """Same rationale as WorkerFleetCollector: business counters live in
    Redis (see orchestrator/metrics.py), read fresh on every scrape rather
    than kept as in-process Python objects, since task state transitions
    happen inside worker processes -- separate from, and often on a
    different machine than, whichever process serves /metrics."""

    def collect(self):
        submitted = CounterMetricFamily(
            "orchestrator_tasks_submitted", "Tasks submitted", labels=["capability"]
        )
        completed = CounterMetricFamily(
            "orchestrator_tasks_completed", "Tasks that reached a terminal state", labels=["status"]
        )
        reclaimed = CounterMetricFamily(
            "orchestrator_tasks_reclaimed",
            "Tasks reclaimed by the reaper after their lease expired (worker crashed/killed)",
        )
        duration = SummaryMetricFamily(
            "orchestrator_task_duration_seconds",
            "Wall-clock time for a task's strategy execution + analysis step",
        )
        try:
            snap = task_queue.metrics.snapshot()
            for capability, count in snap["submitted"].items():
                submitted.add_metric([capability], count)
            for status, count in snap["completed"].items():
                completed.add_metric([status], count)
            reclaimed.add_metric([], snap["reclaimed"])
            duration.add_metric([], snap["duration_count"], snap["duration_sum"])
        except Exception:  # noqa: BLE001
            pass
        yield submitted
        yield completed
        yield reclaimed
        yield duration


REGISTRY.register(WorkerFleetCollector())
REGISTRY.register(TaskMetricsCollector())


@asynccontextmanager
async def lifespan(app: FastAPI):
    reaper.start()
    yield
    reaper.stop()
    worker_manager.stop_all()


app = FastAPI(title="Agent Orchestration Tool", lifespan=lifespan)
app.add_middleware(RateLimitMiddleware)
app.mount("/static", StaticFiles(directory=DASHBOARD_HTML_PATH.parent), name="static")

Instrumentator().instrument(app).expose(app, include_in_schema=False)


@app.get("/health")
def health():
    """Liveness: is this process responsive at all. Deliberately cheap
    (no external calls) -- a container orchestrator restarting on health
    failures shouldn't kill a perfectly fine process just because Redis
    had a momentary blip; that distinction is what /ready is for."""
    return {"status": "ok"}


@app.get("/ready")
def ready():
    """Readiness: can this instance actually do its job right now."""
    from worker.sandbox_executor import is_docker_available

    redis_ok = task_queue.ping()
    sandbox_ok = (not settings.sandbox_enabled) or is_docker_available()

    llm_ok, llm_error = True, None
    try:
        get_llm_provider()
        profile_providers = {
            profile.provider for profile in parse_agent_profiles(settings.agent_profiles_raw)
        }
        configured_providers = profile_providers | {settings.llm_provider.lower()}
        configured_providers.add(control_provider.lower())
        if "ollama" in configured_providers:
            from orchestrator.llm.ollama_provider import is_ollama_available

            if not is_ollama_available(settings.ollama_base_url):
                llm_ok, llm_error = False, f"{settings.ollama_base_url} is not reachable"
    except Exception as exc:  # noqa: BLE001
        llm_ok, llm_error = False, str(exc)

    checks = {"redis": redis_ok, "docker_sandbox": sandbox_ok, "llm": llm_ok}
    body = {"ready": all(checks.values()), "checks": checks, "environment": settings.environment}
    if llm_error:
        body["llm_error"] = llm_error
    return JSONResponse(body, status_code=200 if body["ready"] else 503)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_HTML_PATH.read_text(encoding="utf-8")


@app.post("/workers/scale", response_model=list[WorkerInfo], dependencies=[Depends(require_api_key)])
def scale_workers(req: ScaleRequest):
    try:
        return worker_manager.scale_to(req.count)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/workers", response_model=list[WorkerInfo])
def list_workers():
    return worker_manager.list_workers()


@app.post(
    "/agent-runs",
    response_model=AgentRun,
    dependencies=[Depends(require_api_key)],
)
def start_agent_run(req: AgentRunRequest):
    """Start the complete user goal -> planner -> traders -> reviewer loop."""
    active_workers = [worker for worker in worker_manager.list_workers() if worker.status == "running"]
    if len(active_workers) < req.trader_count:
        try:
            worker_manager.scale_to(req.trader_count)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
    return agent_workflow.start(req)


@app.get("/agent-runs", response_model=list[AgentRun])
def list_agent_runs(limit: int = Query(default=20, ge=1, le=100)):
    return agent_workflow.list_recent(limit)


@app.get("/agent-runs/{run_id}", response_model=AgentRun)
def get_agent_run(run_id: str):
    run = agent_workflow.get(run_id)
    if run is None:
        raise HTTPException(404, "agent run not found")
    return run


@app.get("/agent-runs/{run_id}/events")
def agent_run_events(run_id: str):
    if agent_workflow.get(run_id) is None:
        raise HTTPException(404, "agent run not found")

    def event_stream():
        for event in message_bus.subscribe(run_id):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.delete("/workers/{worker_id}", dependencies=[Depends(require_api_key)])
def stop_worker(worker_id: str):
    ok = worker_manager.stop_worker(worker_id)
    if not ok:
        raise HTTPException(404, "worker not found")
    return {"stopped": worker_id}


@app.post("/jobs", response_model=JobSubmitResponse, dependencies=[Depends(require_api_key)])
def submit_job(req: JobSubmitRequest):
    job_id = uuid.uuid4().hex
    task_queue.register_job(
        job_id,
        meta={
            "job_id": job_id,
            "parent_job_id": req.parent_job_id,
            "kind": req.kind,
            "required_capability": req.required_capability,
        },
    )
    task_ids = []
    for payload in req.tasks:
        task = Task(job_id=job_id, payload=payload, required_capability=req.required_capability)
        task_queue.push_task(task)
        task_ids.append(task.task_id)
    return JobSubmitResponse(job_id=job_id, task_ids=task_ids)


@app.get("/jobs")
def list_jobs(limit: int = Query(default=20, ge=1, le=100)):
    """Recent job history for tracking past runs, not just the one
    currently open in a browser tab."""
    summaries = []
    for job_id, created_at in task_queue.list_recent_jobs(limit=limit):
        summary = task_queue.get_job_summary(job_id)
        if summary is None:
            continue
        summaries.append({"job_id": job_id, "created_at": created_at, **summary})
    return summaries


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    tasks = task_queue.get_job_tasks(job_id)
    if not tasks:
        raise HTTPException(404, "job not found")
    done = [t for t in tasks if t.status == TaskStatus.DONE]
    failed = [t for t in tasks if t.status == TaskStatus.FAILED]
    cancelled = [t for t in tasks if t.status == TaskStatus.CANCELLED]
    return {
        "job_id": job_id,
        "meta": task_queue.get_job_meta(job_id),
        "total": len(tasks),
        "done": len(done),
        "failed": len(failed),
        "cancelled": len(cancelled),
        "pending_or_running": len(tasks) - len(done) - len(failed) - len(cancelled),
        "tasks": tasks,
    }


@app.post("/jobs/{job_id}/cancel", dependencies=[Depends(require_api_key)])
def cancel_job(job_id: str):
    tasks = task_queue.get_job_tasks(job_id)
    if not tasks:
        raise HTTPException(404, "job not found")
    cancelled = sum(1 for t in tasks if task_queue.request_cancel(t.task_id))
    return {"job_id": job_id, "cancelled": cancelled}


@app.post("/jobs/{job_id}/tasks/{task_id}/retry", dependencies=[Depends(require_api_key)])
def retry_task(job_id: str, task_id: str):
    task = task_queue.get_task(task_id)
    if task is None or task.job_id != job_id:
        raise HTTPException(404, "task not found in this job")
    ok = task_queue.retry_task(task_id)
    if not ok:
        raise HTTPException(409, f"task is {task.status.value}, only failed/cancelled tasks can be retried")
    return {"task_id": task_id, "status": "pending"}


@app.post("/jobs/{job_id}/refine", response_model=JobSubmitResponse, dependencies=[Depends(require_api_key)])
def refine_job(job_id: str, top_n: int = Query(default=2, ge=1, le=10)):
    """Coordinator step: analyze a finished job's best results and submit a
    follow-up job with a narrower parameter sweep around them -- one
    agent's output becoming another's input, not just commentary."""
    tasks = task_queue.get_job_tasks(job_id)
    if not tasks:
        raise HTTPException(404, "job not found")
    if any(t.status in (TaskStatus.PENDING, TaskStatus.RUNNING) for t in tasks):
        raise HTTPException(409, "job still has pending/running tasks")

    payloads = propose_refinement(tasks, top_n=top_n)
    if not payloads:
        raise HTTPException(422, "no completed results to refine from")

    new_job_id = uuid.uuid4().hex
    task_queue.register_job(
        new_job_id,
        meta={"job_id": new_job_id, "parent_job_id": job_id, "kind": "refine", "required_capability": "backtest"},
    )
    task_ids = []
    for payload in payloads:
        task = Task(job_id=new_job_id, payload=payload)
        task_queue.push_task(task)
        task_ids.append(task.task_id)
    return JobSubmitResponse(job_id=new_job_id, task_ids=task_ids)


@app.delete("/jobs/{job_id}", dependencies=[Depends(require_api_key)])
def delete_job(job_id: str):
    removed = task_queue.delete_job(job_id)
    if removed == 0 and not task_queue.job_exists(job_id):
        raise HTTPException(404, "job not found")
    return {"job_id": job_id, "tasks_removed": removed}


@app.get("/jobs/{job_id}/events")
def job_events(job_id: str):
    """Server-Sent Events feed of the inter-agent messages published for
    this job, so the dashboard can show task results as they land live."""
    if not task_queue.job_exists(job_id):
        raise HTTPException(404, "job not found")

    def event_stream():
        for event in message_bus.subscribe(job_id):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
