import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client.core import REGISTRY, CounterMetricFamily, GaugeMetricFamily, SummaryMetricFamily
from prometheus_fastapi_instrumentator import Instrumentator

from orchestrator.auth import require_api_key
from orchestrator.config import settings
from orchestrator.coordinator import propose_refinement
from orchestrator.logging_setup import configure_logging
from orchestrator.messaging import MessageBus
from orchestrator.models import (
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

configure_logging()

task_queue = TaskQueue(settings.redis_url)
message_bus = MessageBus(settings.redis_url)
worker_manager = WorkerManager()
reaper = Reaper(task_queue)

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
    return {
        "status": "ok",
        "redis": task_queue.ping(),
        "environment": settings.environment,
    }


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
