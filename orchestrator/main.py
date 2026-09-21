import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from orchestrator.config import settings
from orchestrator.messaging import MessageBus
from orchestrator.models import (
    JobSubmitRequest,
    JobSubmitResponse,
    ScaleRequest,
    Task,
    WorkerInfo,
)
from orchestrator.queue import TaskQueue
from orchestrator.worker_manager import WorkerManager

task_queue = TaskQueue(settings.redis_url)
message_bus = MessageBus(settings.redis_url)
worker_manager = WorkerManager()

DASHBOARD_HTML_PATH = Path(__file__).resolve().parent / "static" / "dashboard.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    worker_manager.stop_all()


app = FastAPI(title="Agent Orchestration Tool", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=DASHBOARD_HTML_PATH.parent), name="static")


@app.get("/health")
def health():
    return {"status": "ok", "redis": task_queue.ping()}


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_HTML_PATH.read_text(encoding="utf-8")


@app.post("/workers/scale", response_model=list[WorkerInfo])
def scale_workers(req: ScaleRequest):
    if req.count < 0:
        raise HTTPException(400, "count must be >= 0")
    return worker_manager.scale_to(req.count)


@app.get("/workers", response_model=list[WorkerInfo])
def list_workers():
    return worker_manager.list_workers()


@app.delete("/workers/{worker_id}")
def stop_worker(worker_id: str):
    ok = worker_manager.stop_worker(worker_id)
    if not ok:
        raise HTTPException(404, "worker not found")
    return {"stopped": worker_id}


@app.post("/jobs", response_model=JobSubmitResponse)
def submit_job(req: JobSubmitRequest):
    if not req.tasks:
        raise HTTPException(400, "tasks must not be empty")
    job_id = uuid.uuid4().hex
    task_queue.register_job(job_id)
    task_ids = []
    for payload in req.tasks:
        task = Task(job_id=job_id, payload=payload)
        task_queue.push_task(task)
        task_ids.append(task.task_id)
    return JobSubmitResponse(job_id=job_id, task_ids=task_ids)


@app.get("/jobs")
def list_jobs(limit: int = 20):
    """Recent job history for tracking past runs, not just the one
    currently open in a browser tab."""
    summaries = []
    for job_id, created_at in task_queue.list_recent_jobs(limit=limit):
        tasks = task_queue.get_job_tasks(job_id)
        done = [t for t in tasks if t.status == "done"]
        failed = [t for t in tasks if t.status == "failed"]
        summaries.append({
            "job_id": job_id,
            "created_at": created_at,
            "total": len(tasks),
            "done": len(done),
            "failed": len(failed),
            "pending_or_running": len(tasks) - len(done) - len(failed),
        })
    return summaries


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    tasks = task_queue.get_job_tasks(job_id)
    if not tasks:
        raise HTTPException(404, "job not found")
    done = [t for t in tasks if t.status == "done"]
    failed = [t for t in tasks if t.status == "failed"]
    return {
        "job_id": job_id,
        "total": len(tasks),
        "done": len(done),
        "failed": len(failed),
        "pending_or_running": len(tasks) - len(done) - len(failed),
        "tasks": tasks,
    }


@app.get("/jobs/{job_id}/events")
def job_events(job_id: str):
    """Server-Sent Events feed of the inter-agent messages published for
    this job, so the dashboard can show task results as they land live."""

    def event_stream():
        for event in message_bus.subscribe(job_id):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
