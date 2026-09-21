import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from orchestrator.config import settings
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
worker_manager = WorkerManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    worker_manager.stop_all()


app = FastAPI(title="Agent Orchestration Tool", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok", "redis": task_queue.ping()}


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
    task_ids = []
    for payload in req.tasks:
        task = Task(job_id=job_id, payload=payload)
        task_queue.push_task(task)
        task_ids.append(task.task_id)
    return JobSubmitResponse(job_id=job_id, task_ids=task_ids)


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
